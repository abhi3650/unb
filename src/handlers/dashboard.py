"""Owner dashboard — zero nested-quote f-strings."""

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
    if action == "all":
        text, kb = _build_dashboard()
        await query.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)
    else:
        status = None if action == "all_tasks" else action
        await _send_task_list(query.message.reply_text, status=status)


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
        "📊 *Dashboard*\n" + divider() + "\n\n"
        "👥 Admins: " + bold(str(len(admins))) + "   "
        "🎬 Uploads: " + bold(str(upload_count)) + "\n\n"
        "📋 *Tasks* \\(" + bold(str(total)) + " total\\)\n"
        "  🟡 Pending   " + bold(str(p)) + "\n"
        "  🔵 Awaiting  " + bold(str(d)) + "\n"
        "  ✅ Verified  " + bold(str(v)) + "\n"
        "  ❌ Rejected  " + bold(str(r))
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
            "📭 No " + escape_md(label) + " tasks found\\.",
            parse_mode="MarkdownV2",
        )
        return

    header = "📋 *" + escape_md(label) + " Tasks* \\(" + str(len(tasks)) + "\\)\n" + divider()
    await reply_fn(header, parse_mode="MarkdownV2")

    for task in tasks[:15]:
        admin_name    = task["full_name"] or task["username"] or str(task["admin_id"])
        title         = task["title"]
        desc          = task["description"] or ""
        created       = fmt_dt(task["created_at"])
        priority_key  = task["priority"] if task["priority"] else "normal"
        priority_label = PRIORITY_LABEL.get(priority_key, "🔵 Normal")
        task_id       = task["id"]

        desc_line = "\n   📝 " + escape_md(desc) if desc else ""
        text = (
            "🆔 " + bold("#" + str(task_id)) + " — " + escape_md(admin_name) + "\n"
            "📌 " + bold(title) + desc_line + "\n"
            "🎯 " + escape_md(priority_label) + "   🕐 " + escape_md(created)
        )

        if task["status"] == "done":
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔍 Verify", callback_data="verify_task_" + str(task_id))
            ]])
            await reply_fn(text, parse_mode="MarkdownV2", reply_markup=kb)
        else:
            await reply_fn(text, parse_mode="MarkdownV2")
