"""Telegram auto-bookkeeping bot (记账机器人).

Run with:
    python bot.py
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, time as dt_time
from typing import Optional

import asyncpg
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.error import Conflict
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from db import (
    get_alias,
    get_daily_stats,
    get_monthly_stats,
    init_db,
    insert_entry,
    list_aliases,
    set_alias,
)
from parser import extract_amounts

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Access helpers ─────────────────────────────────────────────────────────────


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def _is_allowed(update: Update) -> bool:
    """Return True when the user/chat is permitted to use the bot."""
    uid = update.effective_user.id if update.effective_user else None
    cid = update.effective_chat.id if update.effective_chat else None

    if uid and _is_admin(uid):
        return True

    if config.ALLOWED_USER_IDS and uid not in config.ALLOWED_USER_IDS:
        return False
    if config.ALLOWED_CHAT_IDS and cid not in config.ALLOWED_CHAT_IDS:
        return False

    return True


# ── Forward-message helpers ────────────────────────────────────────────────────


def _source_hash(message: Message) -> str:
    """Build a deduplication hash from the message's forwarding metadata."""
    parts: list[str] = []

    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        parts.append(type(origin).__name__)
        sender = getattr(origin, "sender_user", None)
        if sender:
            parts += [str(sender.id), str(int(origin.date.timestamp()))]
        else:
            name = getattr(origin, "sender_user_name", None)
            chat = getattr(origin, "chat", None)
            if name:
                parts += [name, str(int(origin.date.timestamp()))]
            elif chat:
                parts.append(str(chat.id))
                mid = getattr(origin, "message_id", None)
                if mid:
                    parts.append(str(mid))
    elif getattr(message, "forward_from", None):
        parts += [
            "User",
            str(message.forward_from.id),
            str(int(message.forward_date.timestamp())),
        ]
    elif getattr(message, "forward_from_chat", None):
        parts += [
            "Chat",
            str(message.forward_from_chat.id),
            str(message.forward_from_message_id or 0),
        ]
    elif getattr(message, "forward_sender_name", None):
        parts += [
            "Hidden",
            message.forward_sender_name,
            str(int(message.forward_date.timestamp())),
        ]
    else:
        # Fallback: use receiving context (no dedup guarantee)
        parts += ["local", str(message.chat_id), str(message.message_id)]

    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _forward_identity(message: Message) -> tuple[Optional[int], Optional[str]]:
    """Return (telegram_user_id, display_name) for the original sender."""
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        sender = getattr(origin, "sender_user", None)
        if sender:
            name = sender.full_name or sender.username or str(sender.id)
            return sender.id, name
        name = getattr(origin, "sender_user_name", None)
        if name:
            return None, name
        chat = getattr(origin, "chat", None)
        if chat:
            title = getattr(chat, "title", None) or str(chat.id)
            return None, title

    # Legacy fields (Telegram Bot API < 7.0)
    ff = getattr(message, "forward_from", None)
    if ff:
        return ff.id, ff.full_name or ff.username or str(ff.id)
    ffc = getattr(message, "forward_from_chat", None)
    if ffc:
        return None, getattr(ffc, "title", None) or str(ffc.id)
    fsn = getattr(message, "forward_sender_name", None)
    if fsn:
        return None, fsn

    return None, None


# ── Command handlers ───────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await update.message.reply_text(
        "👋 <b>记账机器人</b>\n\n"
        "📌 使用方法：把消息 <b>转发</b> 给我，我会自动识别金额并记账。\n\n"
        "📋 <b>管理命令（仅管理员）：</b>\n"
        "/bindid &lt;关键词&gt; &lt;用户ID&gt; — 绑定关键词到用户\n"
        "/listaliases — 查看所有关键词别名\n"
        "/stats — 当月入账统计",
        parse_mode=ParseMode.HTML,
    )


async def cmd_bindid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 仅管理员可使用此命令")
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("用法：/bindid &lt;关键词&gt; &lt;用户ID&gt;", parse_mode=ParseMode.HTML)
        return

    keyword = args[0]
    try:
        user_id = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ 用户ID 必须是整数")
        return

    await set_alias(keyword, user_id, update.effective_user.id)
    await update.message.reply_text(f"✅ 已绑定：<code>{keyword}</code> → <code>{user_id}</code>", parse_mode=ParseMode.HTML)


async def cmd_listaliases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 仅管理员可使用此命令")
        return

    aliases = await list_aliases()
    if not aliases:
        await update.message.reply_text("暂无别名配置")
        return

    lines = [f"• <code>{kw}</code> → <code>{uid}</code>" for kw, uid in aliases]
    await update.message.reply_text(
        "📋 <b>别名列表</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return

    now = datetime.now(config.TZ)
    stats = await get_monthly_stats(now.year, now.month)
    await update.message.reply_text(
        _fmt_monthly(now.year, now.month, stats),
        parse_mode=ParseMode.HTML,
    )


# ── Forward handler ────────────────────────────────────────────────────────────


async def handle_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return

    message = update.message
    text = message.text or message.caption or ""
    amounts = extract_amounts(text)

    if not amounts:
        await message.reply_text("⚠️ 未识别到有效金额，请检查消息内容")
        return

    src_hash = _source_hash(message)
    fwd_uid, fwd_name = _forward_identity(message)

    # Alias look-up: if we have a name but no UID, try the alias table
    if fwd_uid is None and fwd_name:
        fwd_uid = await get_alias(fwd_name)

    if len(amounts) == 1:
        await _do_record(message, fwd_uid, fwd_name, amounts[0], src_hash)
    else:
        # Multiple candidates — let the user pick
        context.user_data["pending"] = {
            "fwd_uid": fwd_uid,
            "fwd_name": fwd_name,
            "src_hash": src_hash,
            "amounts": amounts,
            "chat_id": message.chat_id,
            "msg_id": message.message_id,
        }
        buttons = [
            [InlineKeyboardButton(f"¥{amt:,.2f}", callback_data=f"amt:{i}")]
            for i, amt in enumerate(amounts)
        ]
        buttons.append([InlineKeyboardButton("❌ 取消", callback_data="amt:cancel")])
        await message.reply_text(
            "📌 检测到多个候选金额，请选择要入账的金额：",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "amt:cancel":
        context.user_data.pop("pending", None)
        await query.edit_message_text("❌ 已取消记账")
        return

    pending = context.user_data.get("pending")
    if not pending:
        await query.edit_message_text("❌ 操作已过期，请重新转发消息")
        return

    try:
        idx = int(query.data.split(":", 1)[1])
        amount = pending["amounts"][idx]
    except (ValueError, IndexError):
        await query.edit_message_text("❌ 无效选择")
        return

    context.user_data.pop("pending", None)

    src_hash = pending["src_hash"]
    inserted = await insert_entry(
        forward_uid=pending["fwd_uid"],
        forward_name=pending["fwd_name"],
        amount=amount,
        chat_id=pending["chat_id"],
        message_id=pending["msg_id"],
        source_hash=src_hash,
    )
    who = pending["fwd_name"] or (str(pending["fwd_uid"]) if pending["fwd_uid"] else "未知")
    if inserted:
        await query.edit_message_text(
            f"✅ 已记账\n👤 来源：{who}\n💰 金额：¥{amount:,.2f}"
        )
    else:
        await query.edit_message_text("⚠️ 该消息已记录过，跳过重复入账")


# ── Internal record helper ─────────────────────────────────────────────────────


async def _do_record(
    message: Message,
    fwd_uid: Optional[int],
    fwd_name: Optional[str],
    amount: float,
    src_hash: str,
) -> None:
    inserted = await insert_entry(
        forward_uid=fwd_uid,
        forward_name=fwd_name,
        amount=amount,
        chat_id=message.chat_id,
        message_id=message.message_id,
        source_hash=src_hash,
    )
    who = fwd_name or (str(fwd_uid) if fwd_uid else "未知")
    if inserted:
        await message.reply_text(
            f"✅ 已记账\n👤 来源：{who}\n💰 金额：¥{amount:,.2f}"
        )
    else:
        await message.reply_text("⚠️ 该消息已记录过，跳过重复入账")


# ── Report formatting ──────────────────────────────────────────────────────────


def _fmt_daily(d: object, stats: dict) -> str:
    lines = [
        f"📊 <b>{d} 入账统计</b>",
        f"💰 总额：¥{stats['total']:,.2f}",
        f"📝 笔数：{stats['count']} 笔",
        "",
        "👥 分人明细：",
    ]
    if stats["persons"]:
        for name, total, cnt in stats["persons"]:
            lines.append(f"  • {name}：¥{total:,.2f}（{cnt} 笔）")
    else:
        lines.append("  （无数据）")
    return "\n".join(lines)


def _fmt_monthly(year: int, month: int, stats: dict) -> str:
    lines = [
        f"📊 <b>{year}年{month}月 入账统计</b>",
        f"💰 总额：¥{stats['total']:,.2f}",
        f"📝 笔数：{stats['count']} 笔",
        "",
        "👥 分人排行：",
    ]
    if stats["persons"]:
        for i, (name, total, cnt) in enumerate(stats["persons"], 1):
            lines.append(f"  {i}. {name}：¥{total:,.2f}（{cnt} 笔）")
    else:
        lines.append("  （无数据）")
    return "\n".join(lines)


# ── Scheduled jobs ─────────────────────────────────────────────────────────────


async def _job_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send yesterday's summary; also send last-month summary on the 1st."""
    if not config.REPORT_CHAT_ID:
        return
    now = datetime.now(config.TZ)
    yesterday = (now - timedelta(days=1)).date()
    stats = await get_daily_stats(yesterday)
    await context.bot.send_message(
        chat_id=config.REPORT_CHAT_ID,
        text=_fmt_daily(yesterday, stats),
        parse_mode=ParseMode.HTML,
    )

    # Monthly report on the 1st of each month
    if now.day == 1:
        last = (now.replace(day=1) - timedelta(days=1))
        mstats = await get_monthly_stats(last.year, last.month)
        await context.bot.send_message(
            chat_id=config.REPORT_CHAT_ID,
            text=_fmt_monthly(last.year, last.month, mstats),
            parse_mode=ParseMode.HTML,
        )


# ── Application setup ──────────────────────────────────────────────────────────


async def _post_init(application: Application) -> None:
    await init_db()
    lock_conn = await asyncpg.connect(config.DATABASE_URL)
    locked = await lock_conn.fetchval(
        "SELECT pg_try_advisory_lock($1)",
        config.POLLING_LOCK_ID,
    )
    if not locked:
        await lock_conn.close()
        raise RuntimeError(
            "another bot instance already holds the polling lock; "
            "stop duplicate replicas or set a different POLLING_LOCK_ID"
        )
    application.bot_data["_polling_lock_conn"] = lock_conn
    logger.info("Acquired polling lock %s", config.POLLING_LOCK_ID)


async def _post_shutdown(application: Application) -> None:
    lock_conn = application.bot_data.pop("_polling_lock_conn", None)
    if lock_conn:
        try:
            await lock_conn.execute(
                "SELECT pg_advisory_unlock($1)",
                config.POLLING_LOCK_ID,
            )
        finally:
            await lock_conn.close()


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.warning(
            "Telegram polling conflict detected during restart overlap; retrying."
        )
        return
    logger.exception("Unhandled Telegram error", exc_info=context.error)


def main() -> None:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("bindid", cmd_bindid))
    app.add_handler(CommandHandler("listaliases", cmd_listaliases))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Forward message handler (text or caption)
    app.add_handler(
        MessageHandler(
            filters.FORWARDED & (filters.TEXT | filters.CAPTION),
            handle_forward,
        )
    )

    # Inline-keyboard callback for amount selection
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^amt:"))
    app.add_error_handler(_on_error)

    # Daily report at 00:00 local time
    midnight = dt_time(0, 0, 0, tzinfo=config.TZ)
    app.job_queue.run_daily(_job_daily, time=midnight)

    logger.info("Bot starting (polling)…")
    try:
        app.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.exception(
            "Telegram polling conflict: only one bot instance can call getUpdates."
        )


if __name__ == "__main__":
    main()
