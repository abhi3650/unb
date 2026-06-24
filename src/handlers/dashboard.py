"""
Owner dashboard — MarkdownV2-clean, no nested f-string quotes.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_ID
import database as db
from utils import escape_md, bold, italic, divider, fmt_dt, STATUS_LABEL, PRIORITY_LABEL

logger = logging.getLogger(__name__)


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Owner only\\.", parse_mode="MarkdownV2")
        return
    text, kb = _build_dashboard()
    await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)


async def dashboard_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    action = query.data.replace("dash_filter_", "")

    if action in ("all", "all_tasks"):
        if action == "all":
            # Show the summary dashboard
            text, kb = _build_dashboard()
            await query.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)
        else:
            await _send_task_list(query.message.reply_text, status=None)
    else:
        await _send_task_list(query.message.reply_text, status=action)


def _build_dashboard():
    stats        = db.get_task_stats()
    admins       = db.get_all_admins()
    upload_count = db.get_upload_count()
    total        = sum(stats.values())

    p = stats.get("pending",  0)
    d = stats.get("done",     0)
    v = stats.get("verified", 0)
    r = stats.get("rejected", 0)

    text = (
        f"📊 *Dashboard*\n"
        f"{divider()}\n\n"
        f"👥 Admins: {bold(str(len(admins)))}   "
        f"🎬 Uploads: {bold(str(upload_count))}\n\n"
        f"📋 *Tasks* \\({bold(str(total))} total\\)\n"
        f"  🟡 Pending   {bold(str(p))}\n"
        f"  🔵 Awaiting  {bold(str(d))}\n"
        f"  ✅ Verified  {bold(str(v))}\n"
        f"  ❌ Rejected  {bold(str(r))}"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟡 Pending",  callback_data="dash_filter_pending"),
            InlineKeyboardButton("🔵 Awaiting", callback_data="dash_filter_done"),
        ],
        [
            InlineKeyboardButton("✅ Verified",  callback_data="dash_filter_verified"),
            InlineKeyboardButton("❌ Rejected",  callback_data="dash_filter_rejected"),
        ],
        [InlineKeyboardButton("📋 All Tasks", callback_data="dash_filter_all_tasks")],
    ])
    return text, kb


async def _send_task_list(reply_fn, status=None):
    tasks = db.get_all_tasks(status=status)
    label = STATUS_LABEL.get(status, "ALL") if status else "ALL"

    if not tasks:
        await reply_fn(
            f"📭 No {escape_md(label)} tasks found\\.",
            parse_mode="MarkdownV2",
        )
        return

    count_esc = escape_md(str(len(tasks)))
    label_esc = escape_md(label)
    await reply_fn(
        f"📋 *{label_esc} Tasks* \\({count_esc}\\)\n{divider()}",
        parse_mode="MarkdownV2",
    )

    for task in tasks[:15]:
        # Extract fields safely before building string
        admin_name = task["full_name"] or task["username"] or str(task["admin_id"])
        task_title = task["title"]
        task_desc  = task["description"] or ""
        created    = fmt_dt(task["created_at"])

        try:
            priority_key = task["priority"] or "normal"
        except (IndexError, KeyError):
            priority_key = "normal"
        priority_label = PRIORITY_LABEL.get(priority_key, "🔵 Normal")

        desc_line = f"\n   📝 {escape_md(task_desc)}" if task_desc else ""
        text = (
            f"🆔 {bold('#' + str(task['id']))} — {escape_md(admin_name)}\n"
            f"📌 {bold(task_title)}{desc_line}\n"
            f"🎯 {escape_md(priority_label)}   🕐 {escape_md(created)}"
        )

        if task["status"] == "done":
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔍 Verify", callback_data=f"verify_task_{task['id']}")
            ]])
            await reply_fn(text, parse_mode="MarkdownV2", reply_markup=kb)
        else:
            await reply_fn(text, parse_mode="MarkdownV2")
