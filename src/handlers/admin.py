"""
Admin-facing handlers: /mytasks, mark_done, uploads, notes.
"""

from typing import Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_ID
import database as db
from utils import escape_md, bold, italic, divider, fmt_dt, STATUS_LABEL, PRIORITY_LABEL


def _get_priority(task) -> str:
    """Safe priority lookup for sqlite3.Row objects."""
    try:
        return task["priority"] or "normal"
    except (IndexError, KeyError):
        return "normal"


def _get_deadline(task) -> str:
    try:
        return task["deadline"] or ""
    except (IndexError, KeyError):
        return ""


def _get_reject_reason(task) -> str:
    try:
        return task["reject_reason"] or ""
    except (IndexError, KeyError):
        return ""


def _task_card(task, show_done_btn: bool = False) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Build a MarkdownV2-safe task card string + optional keyboard."""
    status   = STATUS_LABEL.get(task["status"], task["status"])
    priority = PRIORITY_LABEL.get(_get_priority(task), "🔵 Normal")
    deadline = _get_deadline(task)
    reject   = _get_reject_reason(task)

    desc_line    = f"\n📝 {escape_md(task['description'])}" if task["description"] else ""
    dl_line      = f"\n📅 Deadline: {escape_md(deadline)}" if deadline else ""
    reject_line  = f"\n⚠️ Reason: {italic(reject)}" if reject and task["status"] == "rejected" else ""

    text = (
        f"🆔 Task {bold('#' + str(task['id']))}\n"
        f"📌 {bold(task['title'])}{desc_line}{dl_line}\n"
        f"🎯 Priority: {escape_md(priority)}\n"
        f"📊 Status: {escape_md(status)}{reject_line}\n"
        f"🕐 Assigned: {escape_md(fmt_dt(task['created_at']))}"
    )

    buttons = []
    if show_done_btn:
        buttons.append([InlineKeyboardButton("✅ Mark as Done", callback_data=f"done_task_{task['id']}")])
    buttons.append([InlineKeyboardButton("📝 Add Note", callback_data=f"add_note_{task['id']}")])

    kb = InlineKeyboardMarkup(buttons)
    return text, kb


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
        f"📋 *Your Tasks* \\({len(tasks)} total\\)\n"
        f"{divider()}\n"
        f"🟡 Pending: {bold(str(len(pending)))}  "
        f"❌ Rejected: {bold(str(len(rejected)))}  "
        f"✅ Done: {bold(str(len(done)))}",
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
    query = update.callback_query
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
    priority   = PRIORITY_LABEL.get(_get_priority(task), "Normal")

    await query.edit_message_text(
        f"🔵 *Task \\#{task_id} Submitted\\!*\n\n"
        f"📌 {bold(task['title'])}\n\n"
        "⏳ Waiting for owner verification\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    desc_line = f"\n📝 {escape_md(task['description'])}" if task["description"] else ""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Verify Task", callback_data=f"verify_task_{task_id}")]
    ])

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                f"🔔 *Task Completed\\!*\n\n"
                f"👤 Admin: {bold(admin_name)}\n"
                f"🆔 Task {bold('#' + str(task_id))}\n"
                f"📌 {bold(task['title'])}{desc_line}\n"
                f"🎯 Priority: {escape_md(priority)}\n\n"
                "Tap below to verify 👇"
            ),
            parse_mode="MarkdownV2",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.warning("Could not notify owner about task %d: %s", task_id, exc)


import logging
logger = logging.getLogger(__name__)


# ─── ADD NOTE ───────────────────────────────────────────────────

async def add_note_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    task_id = int(query.data.replace("add_note_", ""))
    context.user_data["note_task_id"]  = task_id
    context.user_data["awaiting_note"] = True
    await query.message.reply_text(
        f"📝 *Add Note to Task \\#{task_id}*\n\nType your note below:",
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

    await update.message.reply_text(
        f"✅ Note added to Task \\#{task_id}\\.",
        parse_mode="MarkdownV2",
    )

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                f"📝 *New Note on Task \\#{task_id}*\n\n"
                f"👤 From: {bold(name)}\n"
                f"📌 Task: {bold(task['title'])}\n\n"
                f"💬 {escape_md(note_text)}"
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
        f"Page {bold(str(page + 1))} of {bold(str(total_pages))} — {bold(str(len(uploads)))} total",
        "",
    ]
    for i, upload in enumerate(page_items, start + 1):
        q         = f" `{upload['quality']}`" if upload["quality"] else ""
        title_esc = escape_md(upload["title"])
        lines.append(f"{i}\\. [{title_esc}]({upload['url']}){q}")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"uploads_page_{page - 1}"))
    if end < len(uploads):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"uploads_page_{page + 1}"))

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
