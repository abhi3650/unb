"""
Owner-only handlers.
Fixes: dead variable, italic() double-escape, task dict access safety.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from config import OWNER_ID
from states import (
    WAITING_TASK_TEXT, WAITING_ADMIN_SELECT, WAITING_ADD_ADMIN,
    WAITING_BROADCAST, WAITING_PRIORITY, WAITING_DEADLINE,
)
import database as db
from utils import escape_md, bold, italic, code, divider, fmt_dt, PRIORITY_LABEL, STATUS_LABEL

logger = logging.getLogger(__name__)


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def owner_only(func):
    """Decorator: reject non-owner callers, return END for ConversationHandlers."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            await update.message.reply_text(
                "⛔ This command is only for the owner\\.",
                parse_mode="MarkdownV2",
            )
            return ConversationHandler.END
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


def _safe_priority(task) -> str:
    try:
        return task["priority"] or "normal"
    except (IndexError, KeyError):
        return "normal"


def _safe_deadline(task) -> str:
    try:
        return task["deadline"] or ""
    except (IndexError, KeyError):
        return ""


# ─── /start ─────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = user.id

    if is_owner(uid):
        name = escape_md(user.first_name)
        text = (
            f"👑 *Welcome back, {name}\\!*\n\n"
            f"{divider()}\n"
            f"🎬 {bold('Upload Notifier Bot')}\n"
            f"{divider()}\n\n"
            "📋 *Owner Commands*\n\n"
            "📌 *Tasks*\n"
            "  /assign — Assign task to admin\n"
            "  /dashboard — Task board \\+ filters\n"
            "  /stats — Bot statistics\n\n"
            "👥 *Admins*\n"
            "  /admins — List admins\n"
            "  /addadmin — Add admin\n"
            "  /removeadmin — Remove admin\n"
            "  /broadcast — Message all admins\n\n"
            "🎬 *Content*\n"
            "  /uploads — Latest scraped uploads"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="dash_filter_all"),
                InlineKeyboardButton("📈 Stats",     callback_data="show_stats"),
            ],
            [InlineKeyboardButton("🎬 Latest Uploads", callback_data="show_uploads")],
        ])
        await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif db.is_admin(uid):
        db.update_admin_name(uid, user.username or "", user.full_name or "")
        name  = escape_md(user.first_name)
        stats = db.get_admin_stats(uid)
        p = stats.get("pending",  0)
        d = stats.get("done",     0)
        v = stats.get("verified", 0)

        text = (
            f"🛡 *Hello, {name}\\!*\n\n"
            f"{divider()}\n"
            f"🎬 {bold('Upload Notifier Bot — Admin Panel')}\n"
            f"{divider()}\n\n"
            f"🟡 Pending: {bold(str(p))}   🔵 Done: {bold(str(d))}   ✅ Verified: {bold(str(v))}\n\n"
            "📋 *Commands*\n"
            "  /mytasks — View your tasks\n"
            "  /uploads — Latest 1TamilMV uploads"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 My Tasks",        callback_data="my_tasks_btn")],
            [InlineKeyboardButton("🎬 Latest Uploads",  callback_data="show_uploads")],
        ])
        await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    else:
        name = escape_md(user.first_name)
        await update.message.reply_text(
            f"👋 *Hello, {name}\\!*\n\n"
            "You are not authorized to use this bot\\.\n"
            "Contact the bot owner to get access\\.",
            parse_mode="MarkdownV2",
        )


# ─── /assign conversation ────────────────────────────────────────

@owner_only
async def assign_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = db.get_all_admins()
    if not admins:
        await update.message.reply_text(
            "⚠️ No admins registered\\. Use /addadmin first\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📝 *New Task Assignment*\n\n"
        "Send the task in this format:\n\n"
        "`Title — Description`\n\n"
        "Example:\n"
        "`Upload Vikram 2024 — Add subtitles and compress to 1080p`\n\n"
        "_The description is optional\\._",
        parse_mode="MarkdownV2",
    )
    return WAITING_TASK_TEXT


async def handle_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if " — " in raw:
        parts = raw.split(" — ", 1)
    elif " - " in raw:
        parts = raw.split(" - ", 1)
    else:
        parts = [raw, ""]

    context.user_data["task_title"] = parts[0].strip()
    context.user_data["task_desc"]  = parts[1].strip() if len(parts) > 1 else ""

    saved_title = bold(context.user_data["task_title"])  # use bold() on raw
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Low",    callback_data="priority_low"),
        InlineKeyboardButton("🔵 Normal", callback_data="priority_normal"),
        InlineKeyboardButton("🔴 High",   callback_data="priority_high"),
    ]])
    await update.message.reply_text(
        f"✅ Task: {saved_title}\n\n"
        "Select the *priority* for this task:",
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
    return WAITING_PRIORITY


async def handle_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["task_priority"] = query.data.replace("priority_", "")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏩ No Deadline", callback_data="deadline_skip")
    ]])
    await query.edit_message_text(
        "📅 *Set a Deadline* \\(optional\\)\n\n"
        "Send the date, e\\.g\\. `25 Jul 2025` or `Tomorrow`\\.\n\n"
        "Or tap *No Deadline* to skip:",
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
    return WAITING_DEADLINE


async def handle_deadline_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_deadline"] = update.message.text.strip()
    return await _show_admin_picker(update, context, via_message=True)


async def handle_deadline_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["task_deadline"] = ""
    return await _show_admin_picker(update, context, via_message=False, query=query)


async def _show_admin_picker(update, context, via_message: bool = True, query=None):
    admins   = db.get_all_admins()
    priority = context.user_data.get("task_priority", "normal")
    title    = context.user_data.get("task_title", "Untitled")
    deadline = context.user_data.get("task_deadline", "")

    pri_label = PRIORITY_LABEL.get(priority, priority)
    dl_text   = escape_md(deadline) if deadline else italic("No deadline")

    buttons = [
        [InlineKeyboardButton(
            f"👤 {admin['full_name'] or admin['username'] or str(admin['user_id'])}",
            callback_data=f"select_admin_{admin['user_id']}"
        )]
        for admin in admins
    ]

    text = (
        f"👤 *Choose admin to assign:*\n\n"
        f"📌 {bold(title)}\n"
        f"🎯 Priority: {escape_md(pri_label)}\n"
        f"📅 Deadline: {dl_text}"
    )
    kb = InlineKeyboardMarkup(buttons)

    if via_message:
        await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)
    else:
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)
    return WAITING_ADMIN_SELECT


async def handle_admin_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    admin_id  = int(query.data.replace("select_admin_", ""))
    title     = context.user_data.get("task_title", "Untitled Task")
    desc      = context.user_data.get("task_desc", "")
    priority  = context.user_data.get("task_priority", "normal")
    deadline  = context.user_data.get("task_deadline", "")

    task_id   = db.create_task(admin_id, title, desc, priority, deadline)
    admin     = db.get_admin(admin_id)
    admin_name = admin["full_name"] or admin["username"] or str(admin_id)
    pri_label  = PRIORITY_LABEL.get(priority, priority)
    dl_text    = escape_md(deadline) if deadline else italic("None")
    desc_line  = f"\n📝 {escape_md(desc)}" if desc else ""
    dl_line    = f"\n📅 Deadline: {escape_md(deadline)}" if deadline else ""

    await query.edit_message_text(
        f"✅ *Task \\#{task_id} Assigned\\!*\n\n"
        f"👤 Admin: {bold(admin_name)}\n"
        f"📌 {bold(title)}{desc_line}\n"
        f"🎯 Priority: {escape_md(pri_label)}\n"
        f"📅 Deadline: {dl_text}",
        parse_mode="MarkdownV2",
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Mark as Done", callback_data=f"done_task_{task_id}")
    ]])
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"📬 *New Task Assigned\\!*\n\n"
                f"🆔 Task {bold('#' + str(task_id))}\n"
                f"📌 {bold(title)}{desc_line}\n"
                f"🎯 Priority: {escape_md(pri_label)}{dl_line}\n\n"
                "Complete it and press the button below 👇"
            ),
            parse_mode="MarkdownV2",
            reply_markup=kb,
        )
    except Exception as exc:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                f"⚠️ *Could not notify admin {escape_md(admin_name)}*\n"
                f"Error: {escape_md(str(exc))}"
            ),
            parse_mode="MarkdownV2",
        )

    return ConversationHandler.END


# ─── TASK VERIFICATION ──────────────────────────────────────────

async def verify_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_owner(query.from_user.id):
        await query.answer("⛔ Only the owner can verify tasks.", show_alert=True)
        return

    task_id = int(query.data.replace("verify_task_", ""))
    task    = db.get_task(task_id)
    if not task:
        await query.edit_message_text("❌ Task not found\\.", parse_mode="MarkdownV2")
        return

    priority   = PRIORITY_LABEL.get(_safe_priority(task), "Normal")
    desc_line  = f"\n📝 {escape_md(task['description'])}" if task["description"] else ""
    done_at    = fmt_dt(task["done_at"]) if task["done_at"] else "—"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve",        callback_data=f"approve_task_{task_id}"),
        InlineKeyboardButton("❌ Not Completed",  callback_data=f"reject_task_{task_id}"),
    ]])
    await query.edit_message_text(
        f"🔍 *Verify Task \\#{task_id}*\n\n"
        f"📌 {bold(task['title'])}{desc_line}\n"
        f"🎯 Priority: {escape_md(priority)}\n"
        f"🕐 Assigned: {escape_md(fmt_dt(task['created_at']))}\n"
        f"🕐 Done at:  {escape_md(done_at)}\n\n"
        "Is this task completed correctly?",
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )


async def handle_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    data = query.data
    if data.startswith("approve_task_"):
        action  = "approve"
        task_id = int(data.replace("approve_task_", ""))
    else:
        action  = "reject"
        task_id = int(data.replace("reject_task_", ""))

    task = db.get_task(task_id)
    if not task:
        await query.edit_message_text("❌ Task not found\\.", parse_mode="MarkdownV2")
        return

    admin_id = task["admin_id"]

    if action == "approve":
        db.update_task_status(task_id, "verified")
        await query.edit_message_text(
            f"✅ *Task \\#{task_id} Approved\\!*\n📌 {escape_md(task['title'])}",
            parse_mode="MarkdownV2",
        )
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "🎉 *Great work\\!*\n\n"
                    f"Your task {bold('#' + str(task_id))} has been *verified and approved* by the owner\\! ✅\n\n"
                    f"📌 {bold(task['title'])}"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    else:  # reject
        context.user_data["reject_task_id"]        = task_id
        context.user_data["reject_admin_id"]        = admin_id
        context.user_data["awaiting_reject_reason"] = True
        await query.edit_message_text(
            f"❌ *Rejecting Task \\#{task_id}*\n\n"
            f"📌 {bold(task['title'])}\n\n"
            "Type the *reason* for rejection so the admin knows what to fix:",
            parse_mode="MarkdownV2",
        )


async def handle_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.user_data.get("awaiting_reject_reason"):
        return

    reason   = update.message.text.strip()
    task_id  = context.user_data.pop("reject_task_id", None)
    admin_id = context.user_data.pop("reject_admin_id", None)
    context.user_data.pop("awaiting_reject_reason", None)

    if not task_id:
        return

    task = db.get_task(task_id)
    if not task:
        await update.message.reply_text("❌ Task not found\\.", parse_mode="MarkdownV2")
        return

    db.update_task_status(task_id, "rejected", reject_reason=reason)

    await update.message.reply_text(
        f"❌ *Task \\#{task_id} Rejected*\n"
        f"📌 {escape_md(task['title'])}\n\n"
        f"📝 Reason sent: {italic(reason)}",
        parse_mode="MarkdownV2",
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Mark as Done Again", callback_data=f"done_task_{task_id}")
    ]])
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"⚠️ *Task \\#{task_id} Rejected*\n\n"
                f"📌 {bold(task['title'])}\n\n"
                f"📝 *Reason:* {italic(reason)}\n\n"
                "Please fix it and press *Done* again 👇"
            ),
            parse_mode="MarkdownV2",
            reply_markup=kb,
        )
    except Exception:
        pass


# ─── /stats ─────────────────────────────────────────────────────

@owner_only
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = _build_stats()
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = _build_stats()
    await query.message.reply_text(text, parse_mode="MarkdownV2")


def _build_stats() -> str:
    task_stats    = db.get_task_stats()
    admins        = db.get_all_admins()
    upload_count  = db.get_upload_count()

    total    = sum(task_stats.values())
    pending  = task_stats.get("pending",  0)
    done     = task_stats.get("done",     0)
    verified = task_stats.get("verified", 0)
    rejected = task_stats.get("rejected", 0)

    lines = [
        "📈 *Bot Statistics*",
        divider(),
        "",
        f"👥 Admins registered: {bold(str(len(admins)))}",
        f"🎬 Uploads tracked:   {bold(str(upload_count))}",
        "",
        f"📋 *Tasks* \\({bold(str(total))} total\\)",
        f"  🟡 Pending:   {bold(str(pending))}",
        f"  🔵 Awaiting:  {bold(str(done))}",
        f"  ✅ Verified:  {bold(str(verified))}",
        f"  ❌ Rejected:  {bold(str(rejected))}",
        "",
        "📊 *Per Admin*",
    ]
    for admin in admins:
        s     = db.get_admin_stats(admin["user_id"])
        name  = admin["full_name"] or admin["username"] or str(admin["user_id"])
        tot   = sum(s.values())
        ver   = s.get("verified", 0)
        lines.append(f"  👤 {bold(name)}: {bold(str(tot))} tasks, {bold(str(ver))} verified")

    if not admins:
        lines.append("  _No admins yet_")

    return "\n".join(lines)


# ─── /admins ────────────────────────────────────────────────────

@owner_only
async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = db.get_all_admins()
    if not admins:
        await update.message.reply_text(
            "📭 No admins yet\\. Use /addadmin to add one\\.",
            parse_mode="MarkdownV2",
        )
        return

    lines = [f"👥 *Registered Admins* \\({len(admins)}\\)", divider()]
    for i, admin in enumerate(admins, 1):
        name  = admin["full_name"] or "Unknown"
        uname = f"@{escape_md(admin['username'])}" if admin["username"] else italic("no username")
        s     = db.get_admin_stats(admin["user_id"])
        tot   = sum(s.values())
        ver   = s.get("verified", 0)
        lines.append(f"{i}\\. {bold(name)} \\({uname}\\)")
        lines.append(f"   🆔 `{admin['user_id']}`  📋 {tot} tasks  ✅ {ver} verified")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


# ─── /addadmin ──────────────────────────────────────────────────

@owner_only
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *Add Admin*\n\n"
        "Send the *Telegram User ID* of the person\\.\n\n"
        "_They must have started the bot at least once\\._\n\n"
        "💡 Tip: Ask them to message @userinfobot",
        parse_mode="MarkdownV2",
    )
    return WAITING_ADD_ADMIN


async def handle_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    text = update.message.text.strip()
    try:
        user_id = int(text)
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid ID\\. Please send a numeric Telegram user ID\\.",
            parse_mode="MarkdownV2",
        )
        return WAITING_ADD_ADMIN

    if user_id == OWNER_ID:
        await update.message.reply_text(
            "⚠️ You cannot add yourself as an admin\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    try:
        chat      = await context.bot.get_chat(user_id)
        full_name = chat.full_name or ""
        username  = chat.username  or ""
        db.add_admin(user_id, username, full_name)
        await update.message.reply_text(
            f"✅ {bold(full_name or str(user_id))} added as admin\\!",
            parse_mode="MarkdownV2",
        )
        # Register admin-scoped commands immediately
        from telegram import BotCommandScopeChat
        from bot import ADMIN_COMMANDS
        try:
            await context.bot.set_my_commands(
                ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=user_id),
            )
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 *You've been added as an Admin\\!*\n\n"
                "Welcome to Upload Notifier Bot\\.\n"
                "Use /start to see your commands\\."
            ),
            parse_mode="MarkdownV2",
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ Could not add admin: `{escape_md(str(exc))}`",
            parse_mode="MarkdownV2",
        )

    return ConversationHandler.END


# ─── /removeadmin ───────────────────────────────────────────────

@owner_only
async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = db.get_all_admins()
    if not admins:
        await update.message.reply_text("📭 No admins to remove\\.", parse_mode="MarkdownV2")
        return ConversationHandler.END

    buttons = [
        [InlineKeyboardButton(
            f"🗑 {admin['full_name'] or admin['username'] or str(admin['user_id'])}",
            callback_data=f"rm_admin_{admin['user_id']}"
        )]
        for admin in admins
    ]
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="rm_admin_cancel")])

    await update.message.reply_text(
        "🗑 *Remove Admin*\n\nSelect the admin to remove:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ConversationHandler.END


async def handle_remove_admin_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    if query.data == "rm_admin_cancel":
        await query.edit_message_text("❌ Cancelled\\.", parse_mode="MarkdownV2")
        return

    user_id = int(query.data.replace("rm_admin_", ""))
    admin   = db.get_admin(user_id)
    if not admin:
        await query.edit_message_text("❌ Admin not found\\.", parse_mode="MarkdownV2")
        return

    name = admin["full_name"] or admin["username"] or str(user_id)
    db.remove_admin(user_id)
    await query.edit_message_text(
        f"✅ {bold(name)} has been removed as admin\\.",
        parse_mode="MarkdownV2",
    )
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="ℹ️ You have been removed as an admin of Upload Notifier Bot\\.",
            parse_mode="MarkdownV2",
        )
    except Exception:
        pass


# ─── /broadcast ─────────────────────────────────────────────────

@owner_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📢 *Broadcast Message*\n\nType the message to send to all admins:",
        parse_mode="MarkdownV2",
    )
    return WAITING_BROADCAST


async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    message      = update.message.text.strip()
    admins       = db.get_all_admins()
    sent, failed = 0, 0

    for admin in admins:
        try:
            await context.bot.send_message(
                chat_id=admin["user_id"],
                text=f"📢 *Broadcast from Owner:*\n\n{escape_md(message)}",
                parse_mode="MarkdownV2",
            )
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 *Broadcast Complete*\n\n"
        f"✅ Sent: {bold(str(sent))}\n"
        f"❌ Failed: {bold(str(failed))}",
        parse_mode="MarkdownV2",
    )
    return ConversationHandler.END
