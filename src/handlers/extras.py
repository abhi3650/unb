"""
Extra features:
  /search <query>       — Search 1TamilMV, show results with pick buttons
  /watchlist            — Show saved watchlist
  /clearwatchlist       — Clear your watchlist
  watch_add_ callback   — Save a movie from digest to watchlist
  watch_remove_ callback— Remove from watchlist
  /status               — Bot uptime, last scrape, admin count
  /setinterval <mins>   — Owner: change scrape interval (persists to DB)
"""

import logging, time
from datetime import timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_ID
import database as db
from utils import escape_md, bold, italic, divider, fmt_dt
from handlers.scraper import search_site, scrape_movie_links, _chunk_lines, _section_keyboard

logger = logging.getLogger(__name__)

NUM_EMOJI = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣"]


def _uptime_str(start_ts: float) -> str:
    delta = timedelta(seconds=int(time.time() - start_ts))
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


# ════════════════════════════════════════════════════════════════
# /search
# ════════════════════════════════════════════════════════════════

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not (db.is_admin(uid) or uid == OWNER_ID):
        await update.message.reply_text("⛔ Not authorized\\.", parse_mode="MarkdownV2")
        return

    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text(
            "🔍 *Search 1TamilMV*\n\nUsage: `/search movie name`\n\nExample:\n`/search Avatar 2025`",
            parse_mode="MarkdownV2",
        )
        return

    msg = await update.message.reply_text(
        "🔍 Searching for " + bold(query) + "\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    results = await search_site(query)
    if not results:
        await msg.edit_text(
            "❌ *No results found* for " + bold(query) + "\n\nTry a different keyword\\.",
            parse_mode="MarkdownV2",
        )
        return

    # Store results so pick callback can find them
    key = "search_" + str(uid)
    context.bot_data[key] = results

    lines = ["🔍 *Search Results for* " + bold(query), divider(), ""]
    buttons = []
    for i, entry in enumerate(results):
        emoji     = NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1)
        eq        = entry["quality"]
        elang     = entry["lang"]
        q         = " `" + eq + "`" if eq else ""
        lang      = " | " + escape_md(elang) if elang else ""
        title_esc = escape_md(entry["title"])
        lines.append(emoji + " " + title_esc + q + lang)
        buttons.append(InlineKeyboardButton(
            NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1),
            callback_data="search_pick_" + str(uid) + "_" + str(i)
        ))

    lines += ["", italic("Tap a number to get download links")]
    kb = InlineKeyboardMarkup([buttons])

    await msg.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def handle_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped a number on a search result."""
    query = update.callback_query
    await query.answer("⏳ Fetching links…")

    parts   = query.data.split("_")   # search_pick_{uid}_{idx}
    uid     = int(parts[2])
    idx     = int(parts[3])
    key     = "search_" + str(uid)
    results = context.bot_data.get(key, [])
    if idx >= len(results):
        await query.answer("Result not found.", show_alert=True)
        return

    entry     = results[idx]
    title     = entry["title"]
    movie_url = entry["url"]
    chat_id   = query.message.chat_id

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

    # Restore search results view
    lines = ["🔍 *Search Results*", divider(), ""]
    buttons = []
    for i, e in enumerate(results):
        emoji     = NUM_EMOJI[i] if i < len(NUM_EMOJI) else str(i + 1)
        eq        = e["quality"]
        elang     = e["lang"]
        q         = " `" + eq + "`" if eq else ""
        lang      = " | " + escape_md(elang) if elang else ""
        title_esc = escape_md(e["title"])
        lines.append(emoji + " " + title_esc + q + lang)
        buttons.append(InlineKeyboardButton(
            emoji, callback_data="search_pick_" + str(uid) + "_" + str(i)
        ))
    lines += ["", italic("Tap a number to get download links")]
    try:
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([buttons]),
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    # Send magnet links
    if magnets:
        wl_token = "wl_" + str(hash(movie_url))[:8]
        context.bot_data[wl_token] = {"title": title, "url": movie_url, "quality": entry.get("quality","")}
        watch_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❤️ Save to Watchlist", callback_data="watch_add_" + str(hash(movie_url))[:8])
        ]])
        mag_lines = ["🧲 *Magnet Links*", "📌 " + bold(title), divider(), ""]
        for i, m in enumerate(magnets, 1):
            mag_lines.append(str(i) + "\\. [" + escape_md(m["label"]) + "](" + m["url"] + ")")
        for chunk_idx, chunk in enumerate(_chunk_lines(mag_lines)):
            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=chunk,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                    reply_markup=watch_kb if chunk_idx == 0 else None,
                )
            except Exception as exc:
                logger.warning("Mag send: %s", exc)
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🧲 *Magnet Links*\n📌 " + bold(title) + "\n" + divider() + "\n\n" + italic("No magnets found"),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    # Send mirror links
    if mirrors:
        from urllib.parse import urlparse
        by_domain: dict = {}
        for m in mirrors:
            by_domain.setdefault(m["domain"], []).append(m)
        dl_lines = ["📥 *Download Links*", "📌 " + bold(title), divider(), ""]
        for domain, items in by_domain.items():
            dl_lines.append("🌐 " + escape_md(domain))
            for i, m in enumerate(items, 1):
                dl_lines.append("  " + str(i) + "\\. [" + escape_md(m["label"]) + "](" + m["url"] + ")")
            dl_lines.append("")
        for chunk in _chunk_lines(dl_lines):
            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=chunk,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.warning("Mirror send: %s", exc)
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="📥 *Download Links*\n📌 " + bold(title) + "\n" + divider() + "\n\n" + italic("No download links found"),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
# WATCHLIST
# ════════════════════════════════════════════════════════════════

async def handle_watch_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback: watch_add_{token} — add movie to watchlist."""
    query = update.callback_query
    uid   = query.from_user.id
    token = query.data.replace("watch_add_", "")
    entry = context.bot_data.get("wl_" + token)

    if not entry:
        await query.answer("Could not find movie data.", show_alert=True)
        return

    added = db.watchlist_add(uid, entry["title"], entry["url"], entry.get("quality", ""))
    if added:
        await query.answer("❤️ Added to your watchlist!", show_alert=True)
    else:
        await query.answer("Already in your watchlist.", show_alert=True)


async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not (db.is_admin(uid) or uid == OWNER_ID):
        await update.message.reply_text("⛔ Not authorized\\.", parse_mode="MarkdownV2")
        return

    items = db.watchlist_get(uid)
    if not items:
        await update.message.reply_text(
            "📭 *Your Watchlist is Empty*\n\n"
            "Tap *❤️ Save to Watchlist* when browsing downloads to add movies here\\.",
            parse_mode="MarkdownV2",
        )
        return

    lines = ["❤️ *Your Watchlist* \\(" + str(len(items)) + "\\)", divider(), ""]
    buttons = []
    for i, item in enumerate(items, 1):
        q         = " `" + item["quality"] + "`" if item["quality"] else ""
        title_esc = escape_md(item["title"])
        iurl      = item["url"]
        lines.append(str(i) + "\\. [" + title_esc + "](" + iurl + ")" + q)
        buttons.append([InlineKeyboardButton(
            "🗑 Remove #" + str(i),
            callback_data="watch_rm_" + str(item["id"])
        )])

    buttons.append([InlineKeyboardButton("🗑 Clear All", callback_data="watch_clear")])
    kb = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def handle_watch_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid     = query.from_user.id
    item_id = int(query.data.replace("watch_rm_", ""))

    # Get item to get its URL for removal
    with db.get_conn() as conn:
        row = conn.execute("SELECT url FROM watchlist WHERE id=? AND user_id=?", (item_id, uid)).fetchone()
    if row:
        db.watchlist_remove(uid, row["url"])
        await query.answer("Removed from watchlist.", show_alert=True)
        # Refresh the list
        items = db.watchlist_get(uid)
        if not items:
            try:
                await query.edit_message_text(
                    "📭 *Watchlist is now empty\\.*",
                    parse_mode="MarkdownV2",
                )
            except Exception:
                pass
        else:
            # Re-render
            lines = ["❤️ *Your Watchlist* \\(" + str(len(items)) + "\\)", divider(), ""]
            buttons = []
            for i, item in enumerate(items, 1):
                q         = " `" + item["quality"] + "`" if item["quality"] else ""
                title_esc = escape_md(item["title"])
                iurl      = item["url"]
                lines.append(str(i) + "\\. [" + title_esc + "](" + iurl + ")" + q)
                buttons.append([InlineKeyboardButton("🗑 Remove #" + str(i), callback_data="watch_rm_" + str(item["id"]))])
            buttons.append([InlineKeyboardButton("🗑 Clear All", callback_data="watch_clear")])
            try:
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(buttons),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
    else:
        await query.answer("Item not found.", show_alert=True)


async def handle_watch_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    db.watchlist_clear(uid)
    try:
        await query.edit_message_text(
            "🗑 *Watchlist cleared\\.*",
            parse_mode="MarkdownV2",
        )
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
# /status
# ════════════════════════════════════════════════════════════════

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not (db.is_admin(uid) or uid == OWNER_ID):
        await update.message.reply_text("⛔ Not authorized\\.", parse_mode="MarkdownV2")
        return

    start_ts  = float(db.config_get("bot_start_time", str(time.time())))
    last_ts   = float(db.config_get("last_scrape_time", "0"))
    interval  = int(db.config_get("scrape_interval", "300"))

    uptime    = _uptime_str(start_ts)
    last_str  = _uptime_str(last_ts) + " ago" if last_ts else "Never"
    admins    = db.get_all_admins()
    uploads   = db.get_upload_count()
    tasks     = db.get_task_stats()
    pending   = tasks.get("pending", 0)

    await update.message.reply_text(
        "📡 *Bot Status*\n" + divider() + "\n\n"
        "🟢 Status: " + bold("Online") + "\n"
        "⏱ Uptime: " + bold(escape_md(uptime)) + "\n"
        "🔍 Last scrape: " + bold(escape_md(last_str)) + "\n"
        "⏰ Scrape interval: every " + bold(str(interval // 60) + " min") + "\n\n"
        "👥 Admins: " + bold(str(len(admins))) + "\n"
        "🎬 Uploads tracked: " + bold(str(uploads)) + "\n"
        "🟡 Pending tasks: " + bold(str(pending)),
        parse_mode="MarkdownV2",
    )


# ════════════════════════════════════════════════════════════════
# /setinterval  (owner only)
# ════════════════════════════════════════════════════════════════

async def setinterval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("⛔ Owner only\\.", parse_mode="MarkdownV2")
        return

    args = context.args or []
    if not args or not args[0].isdigit():
        current = int(db.config_get("scrape_interval", "300")) // 60
        await update.message.reply_text(
            "⏰ *Set Scrape Interval*\n\n"
            "Current: every " + bold(str(current) + " minutes") + "\n\n"
            "Usage: `/setinterval 10`\n"
            "_Minimum 5 minutes, maximum 120 minutes\\._",
            parse_mode="MarkdownV2",
        )
        return

    minutes = int(args[0])
    if minutes < 5:
        await update.message.reply_text(
            "❌ Minimum interval is *5 minutes*\\.", parse_mode="MarkdownV2"
        )
        return
    if minutes > 120:
        await update.message.reply_text(
            "❌ Maximum interval is *120 minutes*\\.", parse_mode="MarkdownV2"
        )
        return

    seconds = minutes * 60
    db.config_set("scrape_interval", str(seconds))

    # Remove existing job and re-schedule
    current_jobs = context.job_queue.get_jobs_by_name("main_scraper")
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_repeating(
        _scraper_job_wrapper,
        interval=seconds,
        first=seconds,
        name="main_scraper",
    )

    await update.message.reply_text(
        "✅ Scrape interval updated to *" + str(minutes) + " minutes*\\.\n\n"
        "The bot will now check 1TamilMV every " + bold(str(minutes) + " min") + "\\.",
        parse_mode="MarkdownV2",
    )


async def _scraper_job_wrapper(context: ContextTypes.DEFAULT_TYPE):
    """Thin wrapper so we can reference this by name for job replacement."""
    from handlers.scraper import start_scraper
    await start_scraper(context)
