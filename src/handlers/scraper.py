"""
1TamilMV scraper — fixed and improved.

BUG FIXES from chat export analysis (2026-06-25):
  BUG 1: Magnet links were showing www.1TamilMV.xxx%20-... mirror URLs, not real magnets.
          Root cause: we were picking up <a href="http://mirror/..."> tags thinking they
          were download links, and their label text was URL-encoded filenames.
          FIX: Only accept href that literally starts with "magnet:" — nothing else.

  BUG 2: Series with 104 episodes flooded chat with 104+ separate messages.
          FIX: Deduplicate magnets by decoded dn= filename, cap at 25, batch into one message.

  BUG 3: "No direct download links found" — 1TamilMV uses mirror redirect links.
          The actual direct links on their post pages are <a> tags pointing to mirror domains
          (1tamilmv.durban, 1tamilmv.cards etc.) with URL-encoded filenames.
          FIX: Treat ALL links from the post page as download mirrors (they ARE the direct links).
               Group by unique file (dn= decoded filename or link label).

  BUG 4/5: Codec-spec entries ("1080p & 720p - AVC/HEVC...") appearing as movie titles.
          FIX: Filter _extract_entry() — title must contain a year OR ≥40% alphabetic chars
               and be at least 15 chars. Pure codec strings fail both tests.
"""

import logging, re, time
from urllib.parse import unquote_plus, urlparse
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
_KEY_TOP    = "digest_top"
_KEY_RECENT = "digest_recent"
_STARTUP_TS = "bot_start_time"
_LAST_SCRAPE = "last_scrape_time"

# Mirror domain pattern — these ARE the download links on 1TamilMV post pages
_MIRROR_DOMAIN = re.compile(
    r"1tamilmv\.(durban|fi|cards|futbol|center|frl|gripe|cymru|immo|"
    r"rodeo|army|gs|earth|rsvp|haus|band|lc|tech|lat|black|blue|pink|"
    r"red|digital|global|online|world|live|stream|click|link|site|web|"
    r"app|info|news|media|studio|tv|film|movie|video|show|play)",
    re.IGNORECASE
)


# ════════════════════════════════════════════════════════════════
# FETCH
# ════════════════════════════════════════════════════════════════

async def fetch_html(url: str = "") -> str:
    """Fetch a URL with mirror fallback for homepage requests."""
    if not url:
        url = SCRAPE_URL
    mirrors = [url, "https://www.1tamilmv.durban", "https://www.1tamilmv.fi"]
    seen: set = set()
    candidates = []
    for u in mirrors:
        base = u.rstrip("/")
        if base not in seen:
            seen.add(base)
            candidates.append(base)

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30, follow_redirects=True) as client:
        for u in candidates:
            try:
                target = u if u.startswith("http") else "https://" + u
                resp   = await client.get(target if "/" in target[8:] else target + "/")
                resp.raise_for_status()
                logger.info("Fetched %s (%d bytes)", u, len(resp.text))
                db.config_set(_LAST_SCRAPE, str(time.time()))
                return resp.text
            except Exception as exc:
                logger.warning("Fetch failed %s: %s", u, exc)
    return ""


# ════════════════════════════════════════════════════════════════
# TITLE QUALITY FILTER (fixes BUG 4 / BUG 5)
# ════════════════════════════════════════════════════════════════

def _is_real_title(title: str) -> bool:
    """
    Return True only if this looks like a real movie/show title.
    Rejects codec-spec strings like '1080p & 720p - AVC / HEVC - (DD+5.1 - 640Kbps) - 8GB + Rips'.
    Rules:
      1. Must be ≥ 15 characters
      2. Must contain a 4-digit year (19xx or 20xx) OR
         have ≥ 40% alphabetic characters (letters / total non-space chars)
    """
    if len(title) < 15:
        return False
    if re.search(r"\b(19|20)\d{2}\b", title):
        return True
    non_space  = re.sub(r"\s+", "", title)
    alpha_cnt  = sum(1 for c in non_space if c.isalpha())
    if len(non_space) == 0:
        return False
    return (alpha_cnt / len(non_space)) >= 0.40


def _extract_entry(a_tag: Tag):
    title = a_tag.get_text(strip=True)
    url   = a_tag.get("href", "").strip()
    if not title or not url or len(title) < 15:
        return None
    if not url.startswith("http"):
        return None
    if re.search(r"/(forums/forum|discover|search|login|leaderboard|profile)", url):
        return None
    if not _is_real_title(title):
        return None   # FIX BUG 4/5: filter codec-spec strings

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


# ════════════════════════════════════════════════════════════════
# HOMEPAGE PARSE
# ════════════════════════════════════════════════════════════════

def parse_sections(html: str) -> dict:
    soup   = BeautifulSoup(html, "lxml")
    result = {"top": [], "recent": []}

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
                if _SEC_TOP in txt:    current = "top"
                elif _SEC_RECENT in txt: current = "recent"
                return
            if not isinstance(node, Tag):
                return
            tag_text = node.get_text(" ", strip=True).lower()
            if len(tag_text) < 80:
                if _SEC_TOP in tag_text:    current = "top"
                elif _SEC_RECENT in tag_text: current = "recent"
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

    if not result["top"] and not result["recent"]:
        logger.warning("Section strategy A found nothing; using fallback.")
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
# SEARCH  (new feature)
# ════════════════════════════════════════════════════════════════

async def search_site(query: str) -> list:
    """
    Search 1TamilMV using their search URL and return up to 8 matching entries.
    Returns list of {"title", "url", "quality", "lang", "size"} dicts.
    """
    search_url = SCRAPE_URL.rstrip("/") + "/?s=" + query.replace(" ", "+")
    html = await fetch_html(search_url)
    if not html:
        return []

    soup    = BeautifulSoup(html, "lxml")
    results = []
    seen: set = set()

    for a in soup.find_all("a", href=True):
        entry = _extract_entry(a)
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            results.append(entry)
        if len(results) >= 8:
            break

    return results


# ════════════════════════════════════════════════════════════════
# MOVIE PAGE SCRAPER  (fixes BUG 1 / BUG 2 / BUG 3)
# ════════════════════════════════════════════════════════════════

def _decode_mirror_label(href: str, fallback: str) -> str:
    """
    Mirror links look like: https://1tamilmv.durban/Movie%20Name%20720p.mkv
    Extract a human-readable label from the URL path.
    """
    try:
        path = urlparse(href).path
        name = unquote_plus(path.split("/")[-1])
        # Remove extension
        name = re.sub(r"\.(mkv|mp4|avi|zip|rar|7z)$", "", name, flags=re.IGNORECASE)
        return name[:80] if name else fallback
    except Exception:
        return fallback


async def scrape_movie_links(movie_url: str) -> dict:
    """
    Fetch a movie post page and extract:
      - magnet links  (href must start with magnet:)   ← FIX BUG 1
      - mirror links  (href matches 1tamilmv.* domains) ← FIX BUG 3

    Deduplication:
      - Magnets: deduplicate by decoded dn= filename     ← FIX BUG 2
      - Mirrors: deduplicate by decoded filename/path

    Returns {"magnets": [...], "mirrors": [...]}
    Each item: {"label": str, "url": str, "domain": str}
    """
    html = await fetch_html(movie_url)
    if not html:
        return {"magnets": [], "mirrors": []}

    soup    = BeautifulSoup(html, "lxml")
    magnets: list = []
    mirrors: list = []
    seen_mag_dn:  set = set()   # deduplicate magnets by filename
    seen_mir_name: set = set()  # deduplicate mirrors by decoded filename

    for a in soup.find_all("a", href=True):
        href  = a.get("href", "").strip()
        label = a.get_text(strip=True)

        # ── Real magnet links ─────────────────────────────────
        if href.startswith("magnet:"):
            dn_m = re.search(r"[&?]dn=([^&]+)", href)
            dn   = unquote_plus(dn_m.group(1)) if dn_m else label or href[:60]
            dn_key = re.sub(r"\s+", " ", dn.lower().strip())
            if dn_key not in seen_mag_dn:
                seen_mag_dn.add(dn_key)
                magnets.append({
                    "label":  dn[:80],
                    "url":    href,
                    "domain": "magnet",
                })
            if len(magnets) >= 25:   # FIX BUG 2: hard cap
                break

        # ── Mirror / direct download links ────────────────────
        elif href.startswith("http") and _MIRROR_DOMAIN.search(href):
            name     = _decode_mirror_label(href, label or href[:60])
            name_key = re.sub(r"\s+", " ", name.lower().strip())
            domain   = urlparse(href).netloc.replace("www.", "")
            if name_key and name_key not in seen_mir_name:
                seen_mir_name.add(name_key)
                mirrors.append({
                    "label":  name,
                    "url":    href,
                    "domain": domain,
                })
            if len(mirrors) >= 30:
                break

    logger.info(
        "Movie page %s → %d magnets, %d mirror links",
        movie_url, len(magnets), len(mirrors)
    )
    return {"magnets": magnets, "mirrors": mirrors}


# ════════════════════════════════════════════════════════════════
# MESSAGE FORMATTING
# ════════════════════════════════════════════════════════════════

def _entry_line(index: int, entry: dict) -> str:
    eq        = entry["quality"]
    elang     = entry["lang"]
    q         = " `" + eq + "`" if eq else ""
    lang      = " | " + escape_md(elang) if elang else ""
    title_esc = escape_md(entry["title"])
    eurl      = entry["url"]
    return str(index) + "\\. [" + title_esc + "](" + eurl + ")" + q + lang


async def _send_to_all(context: ContextTypes.DEFAULT_TYPE, text: str, kb=None):
    ids = list({OWNER_ID} | {a["user_id"] for a in db.get_all_admins()})
    for uid in ids:
        try:
            await context.bot.send_message(
                chat_id=uid, text=text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("Failed notify %s: %s", uid, exc)


def _chunk_lines(lines: list, limit: int = 3800) -> list:
    chunks, current, cur_len = [], [], 0
    for line in lines:
        ln = len(line) + 1
        if cur_len + ln > limit and current:
            chunks.append("\n".join(current))
            current, cur_len = [line], ln
        else:
            current.append(line)
            cur_len += ln
    if current:
        chunks.append("\n".join(current))
    return chunks


# ════════════════════════════════════════════════════════════════
# DIGEST UI
# ════════════════════════════════════════════════════════════════

def _digest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔥 Top Releases",   callback_data="digest_sec_top"),
        InlineKeyboardButton("🆕 Recently Added", callback_data="digest_sec_recent"),
    ]])


def _section_keyboard(section: str, entries: list) -> InlineKeyboardMarkup:
    NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    buttons = [
        InlineKeyboardButton(
            NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1),
            callback_data="digest_pick_" + section + "_" + str(i)
        )
        for i in range(len(entries))
    ]
    return InlineKeyboardMarkup([buttons, [InlineKeyboardButton("⬅️ Back", callback_data="digest_back")]])


# ════════════════════════════════════════════════════════════════
# STARTUP DIGEST
# ════════════════════════════════════════════════════════════════

async def scrape_startup(context: ContextTypes.DEFAULT_TYPE):
    db.config_set(_STARTUP_TS, str(time.time()))
    logger.info("Startup scrape running...")
    html = await fetch_html()
    if not html:
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text="⚠️ *Startup Scrape Failed*\n\nAll mirrors unreachable\\. Check `SCRAPE_URL`\\.",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass
        return

    sections = parse_sections(html)
    top    = sections["top"][:5]
    recent = sections["recent"][:5]

    context.bot_data[_KEY_TOP]    = top
    context.bot_data[_KEY_RECENT] = recent

    for entry in top + recent:
        if not db.upload_exists(entry["url"]):
            db.save_upload(entry["title"], entry["url"], entry["quality"], entry["size"])

    text = (
        "🤖 *Bot Started — 1TamilMV Digest*\n"
        + divider() + "\n\n"
        "🔥 Top Releases This Week: " + bold(str(len(top))) + " titles\n"
        "🆕 Recently Added: " + bold(str(len(recent))) + " titles\n\n"
        "Tap a section below to browse and download 👇"
    )
    ids = list({OWNER_ID} | {a["user_id"] for a in db.get_all_admins()})
    for uid in ids:
        try:
            await context.bot.send_message(
                chat_id=uid, text=text,
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
    query = update.callback_query
    await query.answer()

    sec     = query.data.replace("digest_sec_", "")
    key     = _KEY_TOP if sec == "top" else _KEY_RECENT
    entries = context.bot_data.get(key, [])
    if not entries:
        await query.answer("No data yet — still loading.", show_alert=True)
        return

    label    = "🔥 TOP RELEASES THIS WEEK" if sec == "top" else "🆕 RECENTLY ADDED"
    NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    lines = [bold(label), ""]
    for i, entry in enumerate(entries):
        emoji     = NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1)
        eq        = entry["quality"]
        elang     = entry["lang"]
        q         = " `" + eq + "`" if eq else ""
        lang      = " | " + escape_md(elang) if elang else ""
        title_esc = escape_md(entry["title"])
        lines.append(emoji + " " + title_esc + q + lang)
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
    query = update.callback_query
    await query.answer()
    top    = context.bot_data.get(_KEY_TOP,    [])
    recent = context.bot_data.get(_KEY_RECENT, [])
    text = (
        "🤖 *Bot Started — 1TamilMV Digest*\n"
        + divider() + "\n\n"
        "🔥 Top Releases This Week: " + bold(str(len(top))) + " titles\n"
        "🆕 Recently Added: " + bold(str(len(recent))) + " titles\n\n"
        "Tap a section below to browse and download 👇"
    )
    try:
        await query.edit_message_text(
            text, parse_mode="MarkdownV2",
            reply_markup=_digest_keyboard(),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("Back edit failed: %s", exc)


async def handle_digest_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped a number → scrape movie page → send magnets + mirrors."""
    query = update.callback_query
    await query.answer("⏳ Fetching links…")

    parts   = query.data.split("_")   # digest_pick_top_0
    sec     = parts[2]
    idx     = int(parts[3])
    key     = _KEY_TOP if sec == "top" else _KEY_RECENT
    entries = context.bot_data.get(key, [])
    if idx >= len(entries):
        await query.answer("Entry not found.", show_alert=True)
        return

    entry     = entries[idx]
    movie_url = entry["url"]
    title     = entry["title"]
    chat_id   = query.message.chat_id
    uid       = query.from_user.id

    # Show loading state
    try:
        await query.edit_message_text(
            "🔍 *Fetching links for:*\n"
            "📌 " + bold(title) + "\n\n"
            "⏳ Please wait\\.\\.\\.",
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    links   = await scrape_movie_links(movie_url)
    magnets = links["magnets"]
    mirrors = links["mirrors"]

    # Restore section list
    sec_label = "🔥 TOP RELEASES THIS WEEK" if sec == "top" else "🆕 RECENTLY ADDED"
    NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    lines = [bold(sec_label), ""]
    for i, e in enumerate(entries):
        emoji     = NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1)
        eq        = e["quality"]
        elang     = e["lang"]
        q         = " `" + eq + "`" if eq else ""
        lang      = " | " + escape_md(elang) if elang else ""
        title_esc = escape_md(e["title"])
        lines.append(emoji + " " + title_esc + q + lang)
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

    # ── Magnet links ─────────────────────────────────────────────
    if magnets:
        # Add a watchlist button alongside the links
        watch_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❤️ Add to Watchlist", callback_data="watch_add_" + str(hash(movie_url))[:8]),
        ]])
        context.bot_data["wl_" + str(hash(movie_url))[:8]] = {"title": title, "url": movie_url, "quality": entry.get("quality","")}

        mag_lines = [
            "🧲 *Magnet Links*",
            "📌 " + bold(title),
            divider(),
            "",
        ]
        for i, m in enumerate(magnets, 1):
            label_esc = escape_md(m["label"])
            murl      = m["url"]
            mag_lines.append(str(i) + "\\. [" + label_esc + "](" + murl + ")")

        for chunk in _chunk_lines(mag_lines):
            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=chunk,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.warning("Magnet send failed: %s", exc)
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🧲 *Magnet Links*\n"
                    "📌 " + bold(title) + "\n"
                    + divider() + "\n\n"
                    + italic("No magnet links found on this page")
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    # ── Mirror / Direct links ─────────────────────────────────────
    if mirrors:
        # Group by domain for clarity
        by_domain: dict = {}
        for m in mirrors:
            by_domain.setdefault(m["domain"], []).append(m)

        dl_lines = [
            "📥 *Download Links*",
            "📌 " + bold(title),
            divider(),
            "",
        ]
        for domain, items in by_domain.items():
            dl_lines.append("🌐 " + escape_md(domain))
            for i, m in enumerate(items, 1):
                label_esc = escape_md(m["label"])
                durl      = m["url"]
                dl_lines.append("  " + str(i) + "\\. [" + label_esc + "](" + durl + ")")
            dl_lines.append("")

        # Add watchlist button on the last download message
        watch_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❤️ Save to Watchlist", callback_data="watch_add_" + str(hash(movie_url))[:8]),
        ]])
        context.bot_data["wl_" + str(hash(movie_url))[:8]] = {
            "title": title, "url": movie_url, "quality": entry.get("quality", "")
        }

        for i, chunk in enumerate(_chunk_lines(dl_lines)):
            kb = watch_kb if i == 0 else None
            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=chunk,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                    reply_markup=kb,
                )
            except Exception as exc:
                logger.warning("Mirror send failed: %s", exc)
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📥 *Download Links*\n"
                    "📌 " + bold(title) + "\n"
                    + divider() + "\n\n"
                    + italic("No download links found on this page")
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    logger.info("Links sent for '%s': %d magnets, %d mirrors.", title, len(magnets), len(mirrors))


# ════════════════════════════════════════════════════════════════
# RECURRING SCRAPER
# ════════════════════════════════════════════════════════════════

async def start_scraper(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Scheduled scrape running...")
    html = await fetch_html()
    if not html:
        logger.warning("Scheduled scrape: all mirrors failed.")
        return

    sections    = parse_sections(html)
    all_entries = sections["top"] + sections["recent"]
    seen: set   = set()
    new_entries = []
    for entry in all_entries:
        url = entry["url"]
        if url in seen: continue
        seen.add(url)
        if not db.upload_exists(url):
            db.save_upload(entry["title"], url, entry["quality"], entry["size"])
            new_entries.append(entry)

    for entry in new_entries:
        eq   = entry["quality"]
        elng = entry["lang"]
        esz  = entry["size"]
        etit = entry["title"]
        eurl = entry["url"]
        q    = " `" + eq   + "`"               if eq   else ""
        lang = " | " + escape_md(elng)          if elng else ""
        size = " | " + escape_md(esz)           if esz  else ""
        msg  = (
            "🎬 *New Upload on 1TamilMV\\!*\n\n"
            "📌 " + bold(etit) + q + lang + size + "\n"
            "🔗 [Open / Download](" + eurl + ")"
        )
        await _send_to_all(context, msg)

    if new_entries:
        logger.info("Notified %d new upload(s).", len(new_entries))
    else:
        logger.info("No new uploads.")
