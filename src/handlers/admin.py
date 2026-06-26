"""Admin-facing handlers — zero nested-quote f-strings."""

import logging
from typing import Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_ID
import database as db
from utils import escape_md, bold, italic, divider, fmt_dt, STATUS_LABEL, PRIORITY_LABEL

logger = logging.getLogger(__name__)


def _safe(task, key: str, default: str = "") -> str:
    try:
        v = task[key]
        return v if v else default
    except (IndexError, KeyError):
        return default


def _task_card(task, show_done_btn: bool = False) -> Tuple[str, InlineKeyboardMarkup]:
    status    = STATUS_LABEL.get(_safe(task, "status", "pending"), "Unknown")
    priority  = PRIORITY_LABEL.get(_safe(task, "priority", "normal"), "🔵 Normal")
    title     = _safe(task, "title")
    desc      = _safe(task, "description")
    deadline  = _safe(task, "deadline")
    reject    = _safe(task, "reject_reason")
    created   = fmt_dt(_safe(task, "created_at"))
    task_id   = task["id"]

    title_b   = bold(title)
    tid_b     = bold("#" + str(task_id))
    desc_line = "\n📝 " + escape_md(desc) if desc else ""
    dl_line   = "\n📅 Deadline: " + escape_md(deadline) if deadline else ""
    rej_line  = "\n⚠️ Reason: " + italic(reject) if reject and _safe(task, "status") == "rejected" else ""

    text = (
        "🆔 Task " + tid_b + "\n"
        "📌 " + title_b + desc_line + dl_line + "\n"
        "🎯 Priority: " + escape_md(priority) + "\n"
        "📊 Status: " + escape_md(status) + rej_line + "\n"
        "🕐 Assigned: " + escape_md(created)
    )

    buttons = []
    if show_done_btn:
        buttons.append([InlineKeyboardButton(
            "✅ Mark as Done", callback_data="done_task_" + str(task_id)
        )])
    buttons.append([InlineKeyboardButton(
        "📝 Add Note", callback_data="add_note_" + str(task_id)
    )])
    return text, InlineKeyboardMarkup(buttons)


# ─── /mytasks ───────────────────────────────────────────────────

async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE, via_callback: bool = False):
    if via_callback:
        uid      = update.callback_query.from_user.id
        reply_fn = update.callback_query.message.reply_text
    else:
        uid      = update.effective_user.id
        reply_fn = update.message.reply_text

    if not db.is_admin(uid):
        await reply_fn("⛔ You are not an admin\\.", parse_mode="MarkdownV2")
        return

    tasks = db.get_admin_tasks(uid)
    if not tasks:
        await reply_fn(
            "📭 *No tasks assigned yet\\.*\n\nYou'll be notified when a new task arrives\\!",
            parse_mode="MarkdownV2",
        )
        return

    pending  = [t for t in tasks if t["status"] == "pending"]
    rejected = [t for t in tasks if t["status"] == "rejected"]
    active   = pending + rejected
    done     = [t for t in tasks if t["status"] not in ("pending", "rejected")]

    await reply_fn(
        "📋 *Your Tasks* \\(" + str(len(tasks)) + " total\\)\n" + divider() + "\n"
        "🟡 Pending: " + bold(str(len(pending))) + "  "
        "❌ Rejected: " + bold(str(len(rejected))) + "  "
        "✅ Done: " + bold(str(len(done))),
        parse_mode="MarkdownV2",
    )

    for task in active[:10]:
        text, kb = _task_card(task, show_done_btn=True)
        await reply_fn(text, parse_mode="MarkdownV2", reply_markup=kb)

    for task in done[:5]:
        text, kb = _task_card(task, show_done_btn=False)
        await reply_fn(text, parse_mode="MarkdownV2", reply_markup=kb)


# ─── MARK DONE ──────────────────────────────────────────────────

async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer("Submitting…")

    uid     = query.from_user.id
    task_id = int(query.data.replace("done_task_", ""))
    task    = db.get_task(task_id)

    if not task:
        await query.edit_message_text("❌ Task not found\\.", parse_mode="MarkdownV2")
        return

    if task["admin_id"] != uid:
        await query.answer("⛔ This is not your task.", show_alert=True)
        return

    if task["status"] == "done":
        await query.answer("⏳ Already submitted — awaiting owner verification.", show_alert=True)
        return

    db.update_task_status(task_id, "done")

    admin      = db.get_admin(uid)
    admin_name = admin["full_name"] if admin else str(uid)
    title      = _safe(task, "title")
    priority   = PRIORITY_LABEL.get(_safe(task, "priority", "normal"), "Normal")
    tid_b      = bold("#" + str(task_id))
    title_b    = bold(title)

    await query.edit_message_text(
        "🔵 *Task " + tid_b + " Submitted\\!*\n\n"
        "📌 " + title_b + "\n\n"
        "⏳ Waiting for owner verification\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    desc      = _safe(task, "description")
    desc_line = "\n📝 " + escape_md(desc) if desc else ""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 Verify Task", callback_data="verify_task_" + str(task_id))
    ]])

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                "🔔 *Task Completed\\!*\n\n"
                "👤 Admin: " + bold(admin_name) + "\n"
                "🆔 Task " + tid_b + "\n"
                "📌 " + title_b + desc_line + "\n"
                "🎯 Priority: " + escape_md(priority) + "\n\n"
                "Tap below to verify 👇"
            ),
            parse_mode="MarkdownV2",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.warning("Could not notify owner about task %d: %s", task_id, exc)


# ─── ADD NOTE ───────────────────────────────────────────────────

async def add_note_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    task_id = int(query.data.replace("add_note_", ""))
    context.user_data["note_task_id"]  = task_id
    context.user_data["awaiting_note"] = True
    await query.message.reply_text(
        "📝 *Add Note to Task \\#" + str(task_id) + "*\n\nType your note below:",
        parse_mode="MarkdownV2",
    )


async def receive_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_note"):
        return
    uid     = update.effective_user.id
    task_id = context.user_data.pop("note_task_id", None)
    context.user_data.pop("awaiting_note", None)
    if not task_id:
        return

    note_text = update.message.text.strip()
    db.add_note(task_id, uid, note_text)

    task  = db.get_task(task_id)
    admin = db.get_admin(uid)
    name  = admin["full_name"] if admin else str(uid)
    title = _safe(task, "title") if task else "Unknown"

    await update.message.reply_text(
        "✅ Note added to Task \\#" + str(task_id) + "\\.",
        parse_mode="MarkdownV2",
    )
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                "📝 *New Note on Task \\#" + str(task_id) + "*\n\n"
                "👤 From: " + bold(name) + "\n"
                "📌 Task: " + bold(title) + "\n\n"
                "💬 " + escape_md(note_text)
            ),
            parse_mode="MarkdownV2",
        )
    except Exception:
        pass


# ─── /uploads ───────────────────────────────────────────────────

async def view_uploads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not (db.is_admin(uid) or uid == OWNER_ID):
        await update.message.reply_text("⛔ Not authorized\\.", parse_mode="MarkdownV2")
        return
    await _send_uploads(update.message.reply_text, page=0)


async def uploads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not (db.is_admin(uid) or uid == OWNER_ID):
        return
    data = query.data
    if data == "show_uploads":
        await _send_uploads(query.message.reply_text, page=0)
    elif data.startswith("uploads_page_"):
        page = int(data.replace("uploads_page_", ""))
        await _send_uploads(query.message.reply_text, page=page)


async def _send_uploads(reply_fn, page: int = 0):
    PAGE_SIZE = 8
    uploads   = db.get_recent_uploads(limit=80)

    if not uploads:
        await reply_fn(
            "📭 *No uploads recorded yet\\.*\n\nThe bot checks 1TamilMV every 5 minutes\\.",
            parse_mode="MarkdownV2",
        )
        return

    start       = page * PAGE_SIZE
    end         = start + PAGE_SIZE
    page_items  = uploads[start:end]
    total_pages = (len(uploads) + PAGE_SIZE - 1) // PAGE_SIZE

    lines = [
        "🎬 *Latest 1TamilMV Uploads*",
        divider(),
        "Page " + bold(str(page + 1)) + " of " + bold(str(total_pages)) +
        " — " + bold(str(len(uploads))) + " total",
        "",
    ]
    for i, upload in enumerate(page_items, start + 1):
        q         = " `" + upload["quality"] + "`" if upload["quality"] else ""
        title_esc = escape_md(upload["title"])
        lines.append(str(i) + "\\. [" + title_esc + "](" + upload["url"] + ")" + q)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data="uploads_page_" + str(page - 1)))
    if end < len(uploads):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data="uploads_page_" + str(page + 1)))

    kb = InlineKeyboardMarkup([nav]) if nav else None
    await reply_fn(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
        reply_markup=kb,
    )


# ─── my_tasks BUTTON ────────────────────────────────────────────

async def my_tasks_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await my_tasks(update, context, via_callback=True)
