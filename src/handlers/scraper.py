"""
1TamilMV scraper — section-aware, two-tier:

  scrape_startup()  Called once 5s after bot starts. Sends a digest of:
                    • Top 5 from "🔥 TOP RELEASES THIS WEEK"
                    • Top 5 from "RECENTLY ADDED"
                    Always shows current state regardless of DB.

  start_scraper()   Runs every 5 minutes via job queue. Only notifies
                    entries not already in the DB (new since last check).

Site: 1TamilMV runs IPS (Invision Power Suite) forum software.
The two homepage sections are plain-text-headed widget blocks.
We detect them by walking the DOM for text nodes containing the
header strings, then collecting <a> tags that follow.
"""

import logging
import re
import os
import httpx
from bs4 import BeautifulSoup, NavigableString, Tag
from telegram.ext import ContextTypes
from config import OWNER_ID, SCRAPE_URL
import database as db
from utils import escape_md, bold, italic, divider

logger = logging.getLogger(__name__)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Section header substrings (lower-case for comparison)
_SEC_TOP    = "top releases this week"
_SEC_RECENT = "recently added"


# ─── FETCH ──────────────────────────────────────────────────────

async def fetch_html() -> str:
    """
    Fetch 1TamilMV homepage. Tries SCRAPE_URL first, then known fallbacks.
    Returns HTML string or empty string on total failure.
    """
    # Build ordered, deduplicated mirror list
    candidates = [SCRAPE_URL, "https://www.1tamilmv.durban", "https://www.1tamilmv.fi"]
    seen: set = set()
    urls = []
    for u in candidates:
        base = u.rstrip("/")
        if base not in seen:
            seen.add(base)
            urls.append(base)

    async with httpx.AsyncClient(
        headers=HTTP_HEADERS, timeout=30, follow_redirects=True
    ) as client:
        for url in urls:
            try:
                resp = await client.get(url + "/")
                resp.raise_for_status()
                logger.info("Fetched homepage from %s", url)
                return resp.text
            except Exception as exc:
                logger.warning("Mirror %s failed: %s", url, exc)

    return ""


# ─── PARSE ──────────────────────────────────────────────────────

def _extract_entry(a_tag: Tag):
    """Turn an <a> tag into an upload dict, or None if not a valid entry."""
    title = a_tag.get_text(strip=True)
    url = a_tag.get("href", "").strip()

    if not title or not url or len(title) < 8:
        return None
    if not url.startswith("http"):
        return None  # skip nav/anchor links
    # Skip forum index / section links (they don't contain year patterns)
    if re.search(r"/(forums/forum|discover|search|login|leaderboard)", url):
        return None

    quality_m = re.search(
        r"\b(4K|2160p|1080p|720p|480p|HDRip|WEBRip|WEB-DL|BluRay|DVDRip|TRUE WEB-DL|PreDVD|UHD)\b",
        title, re.IGNORECASE
    )
    quality = quality_m.group(0) if quality_m else ""

    lang_m = re.search(
        r"\b(Tamil|Telugu|Hindi|Malayalam|Kannada|English)\b",
        title, re.IGNORECASE
    )
    lang = lang_m.group(0) if lang_m else ""

    size_m = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB))", title, re.IGNORECASE)
    size = size_m.group(0).strip() if size_m else ""

    return {"title": title, "url": url, "quality": quality, "lang": lang, "size": size}


def parse_sections(html: str) -> dict:
    """
    Parse the IPS forum homepage and return:
      {"top": [entry, ...], "recent": [entry, ...]}
    Each list contains up to 5 entries.

    Strategy A (primary):
      Find the smallest DOM block whose text contains BOTH section headers.
      Walk it top-to-bottom, switching current_section when a header is encountered,
      and collecting <a> entries into the matching section bucket.

    Strategy B (fallback):
      Collect all <a> tags inside <strong>/<b> elements sitewide,
      split first-10 into top(0:5) and recent(5:10).
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict = {"top": [], "recent": []}

    # ── Strategy A ───────────────────────────────────────────────
    target = None
    for tag in soup.find_all(["div", "section", "aside", "article", "li"]):
        text = tag.get_text(" ", strip=True).lower()
        if _SEC_TOP in text and _SEC_RECENT in text:
            if target is None or len(tag.get_text()) < len(target.get_text()):
                target = tag

    if target:
        current = None          # "top" | "recent" | None
        counts = {"top": 0, "recent": 0}

        def _walk(node):
            nonlocal current
            if isinstance(node, NavigableString):
                txt = str(node).strip().lower()
                if _SEC_TOP in txt:
                    current = "top"
                elif _SEC_RECENT in txt:
                    current = "recent"
                return
            if not isinstance(node, Tag):
                return

            tag_text = node.get_text(" ", strip=True).lower()
            # Detect header from short tag text
            if len(tag_text) < 80:
                if _SEC_TOP in tag_text:
                    current = "top"
                elif _SEC_RECENT in tag_text:
                    current = "recent"

            if node.name == "a":
                if current and counts[current] < 5:
                    entry = _extract_entry(node)
                    if entry:
                        result[current].append(entry)
                        counts[current] += 1
                return  # don't recurse into <a>

            for child in node.children:
                _walk(child)

        _walk(target)

    # ── Strategy B (fallback) ────────────────────────────────────
    if not result["top"] and not result["recent"]:
        logger.warning("Strategy A found nothing; using fallback link scrape.")
        seen_urls: set = set()
        all_links = []
        for container in soup.find_all(["strong", "b", "p", "li"]):
            for atag in container.find_all("a", href=True):
                entry = _extract_entry(atag)
                if entry and entry["url"] not in seen_urls:
                    seen_urls.add(entry["url"])
                    all_links.append(entry)
        result["top"]    = all_links[:5]
        result["recent"] = all_links[5:10]

    return result


# ─── MESSAGE FORMATTING ─────────────────────────────────────────

def _entry_line(index: int, entry: dict) -> str:
    """Build one MarkdownV2-safe line for an upload entry."""
    q    = f" `{entry['quality']}`" if entry["quality"] else ""
    lang = f" \\| {escape_md(entry['lang'])}" if entry["lang"] else ""
    size = f" \\| {escape_md(entry['size'])}" if entry["size"] else ""
    title_esc = escape_md(entry["title"])
    # Numbers and dots are safe without escape in this position
    return f"{index}\\. [{title_esc}]({entry['url']}){q}{lang}{size}"


# ─── SEND TO ALL ────────────────────────────────────────────────

async def _send_to_all(context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send a MarkdownV2 message to owner + every admin. Silently skips failures."""
    recipient_ids = list({OWNER_ID} | {a["user_id"] for a in db.get_all_admins()})
    for uid in recipient_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Failed to notify %s: %s", uid, exc)


# ─── STARTUP DIGEST ─────────────────────────────────────────────

async def scrape_startup(context: ContextTypes.DEFAULT_TYPE):
    """
    One-shot startup job. Scrapes both sections and sends a digest.
    Always shows current site state (not filtered by DB).
    Saves new entries to DB so recurring scraper won't re-notify them.
    """
    logger.info("Startup scrape: fetching 1TamilMV homepage...")

    html = await fetch_html()
    if not html:
        logger.error("Startup scrape failed — all mirrors unreachable.")
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(
                    "⚠️ *Startup Scrape Failed*\n\n"
                    "Could not reach 1TamilMV\\.\n"
                    "Check `SCRAPE_URL` in your `\\.env` file\\."
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass
        return

    sections = parse_sections(html)
    top    = sections["top"][:5]
    recent = sections["recent"][:5]

    # Save to DB now so recurring scraper skips these
    for entry in top + recent:
        if not db.upload_exists(entry["url"]):
            db.save_upload(entry["title"], entry["url"], entry["quality"], entry["size"])

    # Build digest
    lines = [
        "🤖 *Bot Started — 1TamilMV Digest*",
        divider(),
        "",
        "🔥 *TOP RELEASES THIS WEEK*",
    ]
    if top:
        for i, entry in enumerate(top, 1):
            lines.append(_entry_line(i, entry))
    else:
        lines.append(italic("Nothing found in this section"))

    lines += ["", "🆕 *RECENTLY ADDED*"]
    if recent:
        for i, entry in enumerate(recent, 1):
            lines.append(_entry_line(i, entry))
    else:
        lines.append(italic("Nothing found in this section"))

    lines += [
        "",
        divider(),
        italic("Monitoring every 5 minutes for new uploads"),
    ]

    await _send_to_all(context, "\n".join(lines))
    logger.info("Startup digest sent: %d top + %d recent entries.", len(top), len(recent))


# ─── RECURRING SCRAPER ──────────────────────────────────────────

async def start_scraper(context: ContextTypes.DEFAULT_TYPE):
    """
    Scheduled every 5 minutes. Notifies only entries not already in the DB.
    """
    logger.info("Scheduled scrape running...")

    html = await fetch_html()
    if not html:
        logger.warning("Scheduled scrape: all mirrors failed.")
        return

    sections = parse_sections(html)
    all_entries = sections["top"] + sections["recent"]

    # Deduplicate within this batch then check DB
    seen_this_batch: set = set()
    new_entries = []
    for entry in all_entries:
        url = entry["url"]
        if url in seen_this_batch:
            continue
        seen_this_batch.add(url)
        if not db.upload_exists(url):
            db.save_upload(entry["title"], url, entry["quality"], entry["size"])
            new_entries.append(entry)

    for entry in new_entries:
        q    = f" `{entry['quality']}`" if entry["quality"] else ""
        lang = f" \\| {escape_md(entry['lang'])}" if entry["lang"] else ""
        size = f" \\| {escape_md(entry['size'])}" if entry["size"] else ""
        msg = (
            f"🎬 *New Upload on 1TamilMV\\!*\n\n"
            f"📌 {bold(entry['title'])}{q}{lang}{size}\n"
            f"🔗 [Open / Download]({entry['url']})"
        )
        await _send_to_all(context, msg)

    if new_entries:
        logger.info("Notified %d new upload(s).", len(new_entries))
    else:
        logger.info("No new uploads.")
