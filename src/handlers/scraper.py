"""
1TamilMV scraper — interactive startup digest + movie page scraper.

Flow:
  scrape_startup()
    → Sends digest message with 2 buttons: [🔥 Top Releases] [🆕 Recently Added]
    → Stores scraped data in context.bot_data["digest_top"] / ["digest_recent"]

  handle_digest_section()   (callback: digest_sec_top / digest_sec_recent)
    → Edits message to show numbered list + number buttons [1][2][3][4][5] + [⬅ Back]

  handle_digest_pick()      (callback: digest_pick_{section}_{index})
    → Fetches the movie's post page
    → Scrapes magnet links and direct download links
    → Sends Page 1: Magnets, Page 2: Direct Links (each as a separate message)

  start_scraper()           (job, every 5 min)
    → Checks for new entries, notifies individually
"""

import logging
import re
import json
import httpx
from bs4 import BeautifulSoup, NavigableString, Tag
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

_SEC_TOP    = "top releases this week"
_SEC_RECENT = "recently added"

# bot_data keys
_KEY_TOP    = "digest_top"
_KEY_RECENT = "digest_recent"


# ════════════════════════════════════════════════════════════════
# FETCH
# ════════════════════════════════════════════════════════════════

async def _fetch(url: str) -> str:
    """GET a URL, try SCRAPE_URL-based mirrors if it's the homepage. Returns HTML or ''."""
    candidates = [url]
    if url.rstrip("/") + "/" in (SCRAPE_URL.rstrip("/") + "/", ""):
        # Homepage: add fallbacks
        for mirror in ["https://www.1tamilmv.durban", "https://www.1tamilmv.fi"]:
            if mirror.rstrip("/") != url.rstrip("/"):
                candidates.append(mirror + "/")

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30, follow_redirects=True) as client:
        for u in candidates:
            try:
                r = await client.get(u)
                r.raise_for_status()
                logger.info("Fetched %s", u)
                return r.text
            except Exception as exc:
                logger.warning("Fetch failed %s: %s", u, exc)
    return ""


async def fetch_html() -> str:
    """Fetch the 1TamilMV homepage with mirror fallback."""
    mirrors = [SCRAPE_URL, "https://www.1tamilmv.durban", "https://www.1tamilmv.fi"]
    seen: set = set()
    urls = []
    for u in mirrors:
        base = u.rstrip("/")
        if base not in seen:
            seen.add(base)
            urls.append(base)

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30, follow_redirects=True) as client:
        for url in urls:
            try:
                r = await client.get(url + "/")
                r.raise_for_status()
                logger.info("Homepage fetched from %s", url)
                return r.text
            except Exception as exc:
                logger.warning("Mirror %s failed: %s", url, exc)
    return ""


# ════════════════════════════════════════════════════════════════
# HOMEPAGE PARSE
# ════════════════════════════════════════════════════════════════

def _extract_entry(a_tag: Tag):
    title = a_tag.get_text(strip=True)
    url   = a_tag.get("href", "").strip()
    if not title or not url or len(title) < 8:
        return None
    if not url.startswith("http"):
        return None
    if re.search(r"/(forums/forum|discover|search|login|leaderboard)", url):
        return None

    qm = re.search(
        r"\b(4K|2160p|1080p|720p|480p|HDRip|WEBRip|WEB-DL|BluRay|DVDRip|TRUE WEB-DL|PreDVD|UHD)\b",
        title, re.IGNORECASE
    )
    lm = re.search(r"\b(Tamil|Telugu|Hindi|Malayalam|Kannada|English)\b", title, re.IGNORECASE)
    sm = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB))", title, re.IGNORECASE)

    return {
        "title":   title,
        "url":     url,
        "quality": qm.group(0) if qm else "",
        "lang":    lm.group(0) if lm else "",
        "size":    sm.group(0).strip() if sm else "",
    }


def parse_sections(html: str) -> dict:
    soup   = BeautifulSoup(html, "lxml")
    result = {"top": [], "recent": []}

    # Strategy A: find smallest block containing both headers
    target = None
    for tag in soup.find_all(["div", "section", "aside", "article", "li"]):
        text = tag.get_text(" ", strip=True).lower()
        if _SEC_TOP in text and _SEC_RECENT in text:
            if target is None or len(tag.get_text()) < len(target.get_text()):
                target = tag

    if target:
        current = None
        counts  = {"top": 0, "recent": 0}

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
                return
            for child in node.children:
                _walk(child)

        _walk(target)

    # Strategy B: fallback
    if not result["top"] and not result["recent"]:
        logger.warning("Strategy A found nothing; using fallback.")
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


# ════════════════════════════════════════════════════════════════
# MOVIE PAGE SCRAPER
# ════════════════════════════════════════════════════════════════

async def scrape_movie_links(movie_url: str) -> dict:
    """
    Fetch a movie post page and extract:
      - magnet links  (magnet:?xt=urn:btih:...)
      - direct links  (.mkv | .mp4 | .avi | .zip | .rar | gdrive | pixeldrain | streamtape etc.)
    Returns {"magnets": [...], "direct": [...]}
    Each item: {"label": str, "url": str}
    """
    html = await _fetch(movie_url)
    if not html:
        return {"magnets": [], "direct": []}

    soup = BeautifulSoup(html, "lxml")
    magnets: list = []
    direct:  list = []
    seen_urls: set = set()

    # Direct download / streaming host patterns
    DIRECT_PATTERNS = re.compile(
        r"\.(mkv|mp4|avi|mov|m4v|zip|rar|7z)(\?|$)|"
        r"(drive\.google\.com|mega\.nz|pixeldrain\.com|gofile\.io|"
        r"streamtape\.com|mixdrop\.co|doodstream\.com|filemoon\.|"
        r"1drv\.ms|mediafire\.com|send\.cm|dropbox\.com|terabox\.com|"
        r"katfile\.com|rapidgator\.net|rosefile\.net|filefactory\.com|"
        r"uploadgig\.com|nitroflare\.com|zippyshare\.com|"
        r"1fichier\.com|turbobit\.net)",
        re.IGNORECASE
    )

    for a in soup.find_all("a", href=True):
        href  = a.get("href", "").strip()
        label = a.get_text(strip=True) or href[:60]

        if not href or href in seen_urls:
            continue
        seen_urls.add(href)

        if href.startswith("magnet:"):
            # Extract a readable label from magnet dn= param
            dn_match = re.search(r"dn=([^&]+)", href)
            mag_label = dn_match.group(1).replace("+", " ") if dn_match else label or "Magnet"
            magnets.append({"label": mag_label[:80], "url": href})

        elif href.startswith("http") and DIRECT_PATTERNS.search(href):
            direct.append({"label": label[:80] or href[:60], "url": href})

    logger.info(
        "Movie page %s → %d magnets, %d direct links",
        movie_url, len(magnets), len(direct)
    )
    return {"magnets": magnets, "direct": direct}


# ════════════════════════════════════════════════════════════════
# STARTUP DIGEST
# ════════════════════════════════════════════════════════════════

def _digest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔥 Top Releases",  callback_data="digest_sec_top"),
        InlineKeyboardButton("🆕 Recently Added", callback_data="digest_sec_recent"),
    ]])


def _section_keyboard(section: str, entries: list) -> InlineKeyboardMarkup:
    """Number buttons 1–N for each entry + Back button."""
    NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    buttons = [
        InlineKeyboardButton(
            NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1),
            callback_data=f"digest_pick_{section}_{i}"
        )
        for i in range(len(entries))
    ]
    rows = [buttons, [InlineKeyboardButton("⬅️ Back", callback_data="digest_back")]]
    return InlineKeyboardMarkup(rows)


async def scrape_startup(context: ContextTypes.DEFAULT_TYPE):
    """One-shot startup job: fetch sections, store in bot_data, send digest with 2 buttons."""
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

    # Store in bot_data so callbacks can look them up without re-scraping
    context.bot_data[_KEY_TOP]    = top
    context.bot_data[_KEY_RECENT] = recent

    # Save new entries to DB so recurring scraper skips them
    for entry in top + recent:
        if not db.upload_exists(entry["url"]):
            db.save_upload(entry["title"], entry["url"], entry["quality"], entry["size"])

    top_count    = escape_md(str(len(top)))
    recent_count = escape_md(str(len(recent)))

    text = (
        "🤖 *Bot Started — 1TamilMV Digest*\n"
        f"{divider()}\n\n"
        f"🔥 Top Releases This Week: {bold(str(len(top)))} titles\n"
        f"🆕 Recently Added: {bold(str(len(recent)))} titles\n\n"
        "Tap a section below to browse and download 👇"
    )

    recipient_ids = list({OWNER_ID} | {a["user_id"] for a in db.get_all_admins()})
    for uid in recipient_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=_digest_keyboard(),
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Failed digest to %s: %s", uid, exc)

    logger.info("Startup digest sent: %d top + %d recent.", len(top), len(recent))


# ════════════════════════════════════════════════════════════════
# DIGEST CALLBACKS
# ════════════════════════════════════════════════════════════════

async def handle_digest_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped 🔥 Top Releases or 🆕 Recently Added."""
    query = update.callback_query
    await query.answer()

    sec     = query.data.replace("digest_sec_", "")          # "top" or "recent"
    key     = _KEY_TOP if sec == "top" else _KEY_RECENT
    entries = context.bot_data.get(key, [])

    if not entries:
        await query.answer("No data yet — scrape is still loading.", show_alert=True)
        return

    label = "🔥 TOP RELEASES THIS WEEK" if sec == "top" else "🆕 RECENTLY ADDED"
    NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    lines = [f"{bold(label)}", ""]
    for i, entry in enumerate(entries):
        emoji     = NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1)
        q         = f" `{entry['quality']}`" if entry["quality"] else ""
        lang      = f" \\| {escape_md(entry['lang'])}" if entry["lang"] else ""
        title_esc = escape_md(entry["title"])
        lines.append(f"{emoji} {title_esc}{q}{lang}")

    lines += ["", italic("Tap a number to get download links")]

    try:
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=_section_keyboard(sec, entries),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("edit_message_text failed: %s", exc)


async def handle_digest_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped ⬅️ Back — restore the 2-button digest view."""
    query = update.callback_query
    await query.answer()

    top    = context.bot_data.get(_KEY_TOP, [])
    recent = context.bot_data.get(_KEY_RECENT, [])

    text = (
        "🤖 *Bot Started — 1TamilMV Digest*\n"
        f"{divider()}\n\n"
        f"🔥 Top Releases This Week: {bold(str(len(top)))} titles\n"
        f"🆕 Recently Added: {bold(str(len(recent)))} titles\n\n"
        "Tap a section below to browse and download 👇"
    )
    try:
        await query.edit_message_text(
            text,
            parse_mode="MarkdownV2",
            reply_markup=_digest_keyboard(),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("Back edit failed: %s", exc)


async def handle_digest_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User tapped a number button → scrape movie page → send magnets + direct links.
    callback_data format: digest_pick_{section}_{index}
    """
    query = update.callback_query
    await query.answer("⏳ Fetching download links…", show_alert=False)

    parts  = query.data.split("_")   # ["digest","pick","top","0"]
    sec    = parts[2]                 # "top" or "recent"
    idx    = int(parts[3])
    key    = _KEY_TOP if sec == "top" else _KEY_RECENT
    entries = context.bot_data.get(key, [])

    if idx >= len(entries):
        await query.answer("Entry not found.", show_alert=True)
        return

    entry     = entries[idx]
    movie_url = entry["url"]
    title     = entry["title"]

    # Let user know we're working
    loading_text = (
        f"🔍 *Fetching links for:*\n"
        f"📌 {bold(title)}\n\n"
        "⏳ Please wait\\.\\.\\."
    )
    try:
        await query.edit_message_text(
            loading_text,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    links = await scrape_movie_links(movie_url)
    magnets = links["magnets"]
    direct  = links["direct"]

    chat_id = query.message.chat_id

    # ── Restore the section list after picking ───────────────────
    # (re-show the section so user can pick another)
    sec_label = "🔥 TOP RELEASES THIS WEEK" if sec == "top" else "🆕 RECENTLY ADDED"
    NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    lines = [f"{bold(sec_label)}", ""]
    for i, e in enumerate(entries):
        emoji     = NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1)
        q         = f" `{e['quality']}`" if e["quality"] else ""
        lang      = f" \\| {escape_md(e['lang'])}" if e["lang"] else ""
        title_esc = escape_md(e["title"])
        lines.append(f"{emoji} {title_esc}{q}{lang}")
    lines += ["", italic("Tap a number to get download links")]
    try:
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=_section_keyboard(sec, entries),
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    # ── Send magnet links ────────────────────────────────────────
    if magnets:
        mag_lines = [
            f"🧲 *Magnet Links*",
            f"📌 {bold(title)}",
            f"{divider()}",
            "",
        ]
        for i, m in enumerate(magnets, 1):
            label_esc = escape_md(m["label"])
            mag_lines.append(f"{i}\\. [{label_esc}]({m['url']})")

        # Telegram has a 4096 char limit; chunk if needed
        chunks = _chunk_lines(mag_lines, limit=3800)
        for chunk in chunks:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.warning("Failed to send magnets: %s", exc)
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🧲 *Magnet Links*\n"
                    f"📌 {bold(title)}\n"
                    f"{divider()}\n\n"
                    f"{italic('No magnet links found on this page')}"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    # ── Send direct download links ───────────────────────────────
    if direct:
        dl_lines = [
            f"📥 *Direct Download Links*",
            f"📌 {bold(title)}",
            f"{divider()}",
            "",
        ]
        for i, d in enumerate(direct, 1):
            label_esc = escape_md(d["label"])
            dl_lines.append(f"{i}\\. [{label_esc}]({d['url']})")

        chunks = _chunk_lines(dl_lines, limit=3800)
        for chunk in chunks:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.warning("Failed to send direct links: %s", exc)
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"📥 *Direct Download Links*\n"
                    f"📌 {bold(title)}\n"
                    f"{divider()}\n\n"
                    f"{italic('No direct download links found on this page')}"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    logger.info(
        "Sent links for '%s': %d magnets, %d direct",
        title, len(magnets), len(direct)
    )


def _chunk_lines(lines: list, limit: int = 3800) -> list:
    """
    Split a list of MarkdownV2 lines into chunks that fit under Telegram's 4096-char limit.
    Each chunk is a joined string.
    """
    chunks = []
    current: list = []
    current_len   = 0
    for line in lines:
        length = len(line) + 1  # +1 for newline
        if current_len + length > limit and current:
            chunks.append("\n".join(current))
            current     = [line]
            current_len = length
        else:
            current.append(line)
            current_len += length
    if current:
        chunks.append("\n".join(current))
    return chunks


# ════════════════════════════════════════════════════════════════
# RECURRING SCRAPER (every 5 min)
# ════════════════════════════════════════════════════════════════

async def _send_to_all(context: ContextTypes.DEFAULT_TYPE, text: str, kb=None):
    recipient_ids = list({OWNER_ID} | {a["user_id"] for a in db.get_all_admins()})
    for uid in recipient_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("Failed to notify %s: %s", uid, exc)


async def start_scraper(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled every 5 minutes. Notifies only truly new entries."""
    logger.info("Scheduled scrape running...")

    html = await fetch_html()
    if not html:
        logger.warning("Scheduled scrape: all mirrors failed.")
        return

    sections    = parse_sections(html)
    all_entries = sections["top"] + sections["recent"]

    seen_batch: set = set()
    new_entries = []
    for entry in all_entries:
        url = entry["url"]
        if url in seen_batch:
            continue
        seen_batch.add(url)
        if not db.upload_exists(url):
            db.save_upload(entry["title"], url, entry["quality"], entry["size"])
            new_entries.append(entry)

    for entry in new_entries:
        q    = f" `{entry['quality']}`" if entry["quality"] else ""
        lang = f" \\| {escape_md(entry['lang'])}" if entry["lang"] else ""
        size = f" \\| {escape_md(entry['size'])}" if entry["size"] else ""
        msg  = (
            f"🎬 *New Upload on 1TamilMV\\!*\n\n"
            f"📌 {bold(entry['title'])}{q}{lang}{size}\n"
            f"🔗 [Open / Download]({entry['url']})"
        )
        await _send_to_all(context, msg)

    if new_entries:
        logger.info("Notified %d new upload(s).", len(new_entries))
    else:
        logger.info("No new uploads.")
