"""Telegram auto-bookkeeping bot (记账机器人).

Run with:
    python bot.py
"""
from __future__ import annotations

import logging
import hashlib
import html
from datetime import date, datetime, timedelta, time as dt_time
from typing import Optional

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
    clear_entries_by_forward_uid_and_project,
    clear_entries_by_forward_uid,
    get_daily_stats_for_user,
    get_alias,
    get_alias_keyword_for_user,
    get_daily_stats,
    get_range_stats,
    get_range_stats_for_user,
    get_running_total_for_source,
    get_monthly_stats,
    get_monthly_stats_for_user,
    init_db,
    insert_entry,
    list_allowed_users,
    list_aliases,
    list_project_aliases,
    remove_alias,
    remove_allowed_user,
    remove_project_alias,
    resolve_project_by_text,
    set_alias,
    set_project_alias,
    upsert_allowed_user,
)
from parser import extract_amounts, extract_project_name

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
runtime_allowed_user_ids: set[int] = set(config.ALLOWED_USER_IDS)

# ── Access helpers ─────────────────────────────────────────────────────────────


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def _is_allowed(update: Update) -> bool:
    """Return True when the user/chat is permitted to use the bot."""
    uid = update.effective_user.id if update.effective_user else None
    cid = update.effective_chat.id if update.effective_chat else None

    if uid and _is_admin(uid):
        return True

    if runtime_allowed_user_ids and uid not in runtime_allowed_user_ids:
        return False
    if config.ALLOWED_CHAT_IDS and cid not in config.ALLOWED_CHAT_IDS:
        return False

    return True


def _main_menu_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    if is_admin:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("➕ 绑定用户关键词", callback_data="menu:bindid"),
                    InlineKeyboardButton("📋 查看用户关键词", callback_data="menu:listaliases"),
                ],
                [
                    InlineKeyboardButton("➕ 绑定项目关键词", callback_data="menu:bindproject"),
                    InlineKeyboardButton("📋 查看项目关键词", callback_data="menu:listprojects"),
                ],
                [
                    InlineKeyboardButton("🧹 清空用户记账", callback_data="menu:clearuser"),
                    InlineKeyboardButton("🧹 清空用户项目记账", callback_data="menu:clearuserproject"),
                ],
                [
                    InlineKeyboardButton("➕ 添加可用用户", callback_data="menu:allowadd"),
                    InlineKeyboardButton("➖ 删除可用用户", callback_data="menu:allowremove"),
                ],
                [
                    InlineKeyboardButton("📋 查看可用用户列表", callback_data="menu:allowlist"),
                ],
                [
                    InlineKeyboardButton("📊 今日统计", callback_data="menu:todaystats"),
                    InlineKeyboardButton("📊 本周统计", callback_data="menu:weekstats"),
                ],
                [
                    InlineKeyboardButton("📊 本月统计", callback_data="menu:stats"),
                    InlineKeyboardButton("🔎 日期查账", callback_data="menu:datestats"),
                ],
                [
                    InlineKeyboardButton("📊 按用户统计", callback_data="menu:statsuser"),
                ],
                [InlineKeyboardButton("🔄 刷新菜单", callback_data="menu:home")],
            ]
        )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ 绑定用户关键词", callback_data="menu:bindid"),
                InlineKeyboardButton("📋 查看用户关键词", callback_data="menu:listaliases"),
            ],
            [
                InlineKeyboardButton("➕ 绑定项目关键词", callback_data="menu:bindproject"),
                InlineKeyboardButton("📋 查看项目关键词", callback_data="menu:listprojects"),
            ],
            [InlineKeyboardButton("🧹 清空我的记账", callback_data="menu:clearself")],
            [
                InlineKeyboardButton("📊 今日统计", callback_data="menu:todaystats"),
                InlineKeyboardButton("📊 本周统计", callback_data="menu:weekstats"),
            ],
            [
                InlineKeyboardButton("📊 本月统计", callback_data="menu:stats"),
                InlineKeyboardButton("🔎 日期查账", callback_data="menu:datestats"),
            ],
            [InlineKeyboardButton("🔄 刷新菜单", callback_data="menu:home")],
        ]
    )


async def _get_user_nickname(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Optional[str]:
    try:
        chat = await context.bot.get_chat(user_id)
    except Exception:
        return None
    return (
        getattr(chat, "full_name", None)
        or getattr(chat, "title", None)
        or (f"@{chat.username}" if getattr(chat, "username", None) else None)
    )


def _fmt_uid_with_nickname(user_id: int, nickname: Optional[str]) -> str:
    if nickname:
        return f"<code>{user_id}</code>（{html.escape(nickname)}）"
    return f"<code>{user_id}</code>"


async def _fmt_allowed_user_ids(context: ContextTypes.DEFAULT_TYPE) -> str:
    if not runtime_allowed_user_ids:
        return "（未配置，当前不限制用户白名单）"
    ids = sorted(runtime_allowed_user_ids)
    lines: list[str] = []
    for uid in ids:
        nickname = await _get_user_nickname(context, uid)
        lines.append(f"• {_fmt_uid_with_nickname(uid, nickname)}")
    return "\n".join(lines)


def _cancel_flow_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("↩️ 返回主菜单", callback_data="menu:home")]]
    )


def _menu_text(is_admin: bool) -> str:
    base = (
        "👋 <b>记账机器人</b>\n\n"
        "📌 将消息转发给我即可自动记账。\n"
        "📌 所有功能请使用下方内联按钮操作。"
    )
    if is_admin:
        return base + "\n\n🔐 当前身份：管理员"
    return base


def _clear_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "flow_action",
        "flow_keyword",
        "flow_user_id",
        "alias_delete_map",
        "project_delete_map",
    ):
        context.user_data.pop(key, None)


async def _build_alias_list_view(
    context: ContextTypes.DEFAULT_TYPE,
    is_admin: bool,
    current_user_id: int,
) -> tuple[str, InlineKeyboardMarkup, dict[str, str]]:
    aliases = await list_aliases(owner_user_id=None if is_admin else current_user_id)
    lines: list[str] = []
    delete_map: dict[str, str] = {}
    buttons: list[list[InlineKeyboardButton]] = []
    for index, (kw, uid) in enumerate(aliases):
        nickname = await _get_user_nickname(context, uid)
        lines.append(
            f"• <code>{html.escape(kw)}</code> → {_fmt_uid_with_nickname(uid, nickname)}"
        )
        token = hashlib.sha1(f"{index}:{kw}".encode("utf-8")).hexdigest()[:10]
        delete_map[token] = kw
        button_kw = kw if len(kw) <= 12 else f"{kw[:12]}…"
        buttons.append(
            [InlineKeyboardButton(f"🗑 删除「{button_kw}」", callback_data=f"menu:delalias:{token}")]
        )
    buttons.append([InlineKeyboardButton("↩️ 返回主菜单", callback_data="menu:home")])
    text = (
        "暂无用户关键词配置"
        if not aliases
        else "📋 <b>用户关键词列表</b>\n" + "\n".join(lines)
    )
    return text, InlineKeyboardMarkup(buttons), delete_map


async def _build_project_list_view(
    is_admin: bool,
    current_user_id: int,
) -> tuple[str, InlineKeyboardMarkup, dict[str, str]]:
    projects = await list_project_aliases(owner_user_id=None if is_admin else current_user_id)
    delete_map: dict[str, str] = {}
    buttons: list[list[InlineKeyboardButton]] = []
    for index, (kw, _project) in enumerate(projects):
        token = hashlib.sha1(f"{index}:{kw}".encode("utf-8")).hexdigest()[:10]
        delete_map[token] = kw
        button_kw = kw if len(kw) <= 12 else f"{kw[:12]}…"
        buttons.append(
            [InlineKeyboardButton(f"🗑 删除「{button_kw}」", callback_data=f"menu:delproject:{token}")]
        )
    buttons.append([InlineKeyboardButton("↩️ 返回主菜单", callback_data="menu:home")])
    text = (
        "暂无项目关键词配置"
        if not projects
        else "📋 <b>项目关键词列表</b>\n"
        + "\n".join(
            f"• <code>{html.escape(kw)}</code> → <code>{html.escape(project)}</code>"
            for kw, project in projects
        )
    )
    return text, InlineKeyboardMarkup(buttons), delete_map


def _fmt_signed_amount(amount: float) -> str:
    return f"-¥{abs(amount):,.2f}" if amount < 0 else f"¥{amount:,.2f}"


def _week_bounds(target_date: date) -> tuple[date, date]:
    start = target_date - timedelta(days=target_date.weekday())
    end = start + timedelta(days=6)
    return start, end


def _parse_local_date(raw: str) -> Optional[date]:
    value = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    if "年" in value and "月" in value and "日" in value:
        try:
            normalized = value.replace("年", "-").replace("月", "-").replace("日", "")
            return datetime.strptime(normalized, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


# ── Forward-message helpers ────────────────────────────────────────────────────


def _source_hash(message: Message) -> str:
    """Build a deduplication hash from the message's forwarding metadata."""
    parts: list[str] = []
    payload = (message.text or message.caption or "").strip()
    payload_hash = hashlib.sha256(payload.encode()).hexdigest() if payload else ""

    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        parts.append(type(origin).__name__)
        sender = getattr(origin, "sender_user", None)
        if sender:
            parts += [str(sender.id), str(int(origin.date.timestamp())), payload_hash]
        else:
            name = getattr(origin, "sender_user_name", None)
            chat = getattr(origin, "chat", None)
            if name:
                parts += [name, str(int(origin.date.timestamp())), payload_hash]
            elif chat:
                parts.append(str(chat.id))
                mid = getattr(origin, "message_id", None)
                if mid:
                    parts.append(str(mid))
                parts.append(payload_hash)
    elif getattr(message, "forward_from", None):
        parts += [
            "User",
            str(message.forward_from.id),
            str(int(message.forward_date.timestamp())),
            payload_hash,
        ]
    elif getattr(message, "forward_from_chat", None):
        parts += [
            "Chat",
            str(message.forward_from_chat.id),
            str(message.forward_from_message_id or 0),
            payload_hash,
        ]
    elif getattr(message, "forward_sender_name", None):
        parts += [
            "Hidden",
            message.forward_sender_name,
            str(int(message.forward_date.timestamp())),
            payload_hash,
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
    _clear_flow(context)
    message = update.message
    is_admin = bool(update.effective_user and _is_admin(update.effective_user.id))
    await message.reply_text(
        _menu_text(is_admin),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_menu_keyboard(is_admin),
    )


async def _reply_inline_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_flow(context)
    is_admin = bool(update.effective_user and _is_admin(update.effective_user.id))
    await update.message.reply_text(
        "请使用内联按钮完成操作：先发送 /start 打开主菜单。",
        reply_markup=_main_menu_keyboard(is_admin),
    )


async def cmd_bindid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await _reply_inline_only(update, context)


async def cmd_listaliases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await _reply_inline_only(update, context)


async def cmd_bindproject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await _reply_inline_only(update, context)


async def cmd_listprojects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await _reply_inline_only(update, context)


async def cmd_clearuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await _reply_inline_only(update, context)


async def cmd_clearuserproject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await _reply_inline_only(update, context)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    await _reply_inline_only(update, context)


async def handle_private_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return

    action = context.user_data.get("flow_action")
    if not action:
        return

    is_admin = bool(update.effective_user and _is_admin(update.effective_user.id))
    if not is_admin and action not in {
        "bindproject_keyword",
        "bindproject_name",
        "bindid_keyword",
        "bindid_user",
        "delalias_keyword",
        "delproject_keyword",
        "stats_date",
    }:
        _clear_flow(context)
        await update.message.reply_text("❌ 仅管理员可执行该操作")
        return

    raw = (update.message.text or "").strip()
    if not raw:
        await update.message.reply_text("❌ 输入不能为空，请重试")
        return

    if action == "bindid_keyword":
        context.user_data["flow_keyword"] = raw
        context.user_data["flow_action"] = "bindid_user"
        await update.message.reply_text("请输入要绑定的用户ID（整数）", reply_markup=_cancel_flow_keyboard())
        return

    if action == "bindid_user":
        try:
            target_uid = int(raw)
        except ValueError:
            await update.message.reply_text("❌ 用户ID 必须是整数，请重新输入")
            return
        keyword = context.user_data.get("flow_keyword", "").strip()
        owner_user_id = 0 if is_admin else update.effective_user.id
        await set_alias(keyword, target_uid, update.effective_user.id, owner_user_id=owner_user_id)
        nickname = await _get_user_nickname(context, target_uid)
        user_label = _fmt_uid_with_nickname(target_uid, nickname)
        _clear_flow(context)
        await update.message.reply_text(
            f"✅ 已绑定：<code>{keyword}</code> → {user_label}",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(is_admin),
        )
        return

    if action == "bindproject_keyword":
        context.user_data["flow_keyword"] = raw
        context.user_data["flow_action"] = "bindproject_name"
        await update.message.reply_text("请输入项目名", reply_markup=_cancel_flow_keyboard())
        return

    if action == "bindproject_name":
        keyword = context.user_data.get("flow_keyword", "").strip()
        owner_user_id = 0 if is_admin else update.effective_user.id
        await set_project_alias(keyword, raw, update.effective_user.id, owner_user_id=owner_user_id)
        _clear_flow(context)
        await update.message.reply_text(
            f"✅ 已绑定项目关键词：<code>{keyword}</code> → <code>{raw}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(is_admin),
        )
        return

    if action == "delalias_keyword":
        owner_user_id = 0 if is_admin else update.effective_user.id
        removed = await remove_alias(raw, owner_user_id=owner_user_id)
        _clear_flow(context)
        msg = (
            f"✅ 已删除用户关键词：<code>{raw}</code>"
            if removed
            else f"ℹ️ 用户关键词 <code>{raw}</code> 不存在，已保持现状"
        )
        await update.message.reply_text(
            msg,
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(is_admin),
        )
        return

    if action == "delproject_keyword":
        owner_user_id = 0 if is_admin else update.effective_user.id
        removed = await remove_project_alias(raw, owner_user_id=owner_user_id)
        _clear_flow(context)
        msg = (
            f"✅ 已删除项目关键词：<code>{raw}</code>"
            if removed
            else f"ℹ️ 项目关键词 <code>{raw}</code> 不存在，已保持现状"
        )
        await update.message.reply_text(
            msg,
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(is_admin),
        )
        return

    if action == "clearuser_uid":
        try:
            target_uid = int(raw)
        except ValueError:
            await update.message.reply_text("❌ 用户ID 必须是整数，请重新输入")
            return
        deleted = await clear_entries_by_forward_uid(target_uid)
        _clear_flow(context)
        await update.message.reply_text(
            f"✅ 已清空用户 <code>{target_uid}</code> 的记账，共删除 <b>{deleted}</b> 条",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(True),
        )
        return

    if action == "clearuserproject_uid":
        try:
            target_uid = int(raw)
        except ValueError:
            await update.message.reply_text("❌ 用户ID 必须是整数，请重新输入")
            return
        context.user_data["flow_user_id"] = target_uid
        context.user_data["flow_action"] = "clearuserproject_name"
        await update.message.reply_text("请输入项目名", reply_markup=_cancel_flow_keyboard())
        return

    if action == "clearuserproject_name":
        target_uid = context.user_data.get("flow_user_id")
        deleted = await clear_entries_by_forward_uid_and_project(target_uid, raw)
        _clear_flow(context)
        await update.message.reply_text(
            f"✅ 已清空用户 <code>{target_uid}</code> 在项目 <code>{raw}</code> 的记账，共删除 <b>{deleted}</b> 条",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(True),
        )
        return

    if action == "allow_add":
        try:
            target_uid = int(raw)
        except ValueError:
            await update.message.reply_text("❌ 用户ID 必须是整数，请重新输入")
            return
        await upsert_allowed_user(target_uid, update.effective_user.id)
        runtime_allowed_user_ids.add(target_uid)
        _clear_flow(context)
        await update.message.reply_text(
            f"✅ 已添加可用用户：<code>{target_uid}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(True),
        )
        return

    if action == "allow_remove":
        try:
            target_uid = int(raw)
        except ValueError:
            await update.message.reply_text("❌ 用户ID 必须是整数，请重新输入")
            return
        removed = await remove_allowed_user(target_uid)
        runtime_allowed_user_ids.discard(target_uid)
        _clear_flow(context)
        msg = (
            f"✅ 已删除可用用户：<code>{target_uid}</code>"
            if removed
            else f"ℹ️ 用户 <code>{target_uid}</code> 不在可用列表中，已保持现状"
        )
        await update.message.reply_text(
            msg,
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(True),
        )
        return

    if action == "stats_user":
        try:
            target_uid = int(raw)
        except ValueError:
            await update.message.reply_text("❌ 用户ID 必须是整数，请重新输入")
            return
        now = datetime.now(config.TZ)
        stats = await get_monthly_stats_for_user(now.year, now.month, target_uid)
        _clear_flow(context)
        await update.message.reply_text(
            _fmt_monthly_user(now.year, now.month, target_uid, stats),
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(True),
        )
        return

    if action == "stats_date":
        target_date = _parse_local_date(raw)
        if not target_date:
            await update.message.reply_text("❌ 日期格式错误，请输入如 2026-06-25")
            return
        if is_admin:
            stats = await get_daily_stats(target_date)
            text = _fmt_daily(target_date, stats)
            menu = _main_menu_keyboard(True)
        else:
            stats = await get_daily_stats_for_user(target_date, update.effective_user.id)
            text = _fmt_daily_user(target_date, update.effective_user.id, stats)
            menu = _main_menu_keyboard(False)
        _clear_flow(context)
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=menu,
        )
        return


# ── Forward handler ────────────────────────────────────────────────────────────


async def handle_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("❌ 无权限访问")
        return

    message = update.message
    text = message.text or message.caption or ""
    amounts = extract_amounts(text)
    current_user = update.effective_user
    current_uid = current_user.id if current_user else None
    is_admin = bool(current_uid and _is_admin(current_uid))

    if not amounts:
        await message.reply_text("⚠️ 未识别到有效金额，请检查消息内容")
        return

    src_hash = _source_hash(message)
    fwd_uid, fwd_name = _forward_identity(message)

    # Alias look-up: if we have a name but no UID, try the alias table
    if not is_admin and current_uid:
        fwd_uid = current_uid
        fwd_name = current_user.full_name or current_user.username or str(current_uid)
    elif fwd_uid is None and fwd_name:
        fwd_uid = await get_alias(fwd_name, owner_user_id=None if is_admin else current_uid)

    project_owner_user_id = fwd_uid if fwd_uid is not None else (None if is_admin else current_uid)
    project_name = extract_project_name(text)
    if not project_name:
        project_name = await resolve_project_by_text(text, owner_user_id=project_owner_user_id)
    if not project_name and fwd_uid is not None:
        project_name = await get_alias_keyword_for_user(fwd_uid, owner_user_id=fwd_uid)
    if not project_name:
        project_name = config.DEFAULT_PROJECT_NAME

    if len(amounts) > 1 and any(op in text for op in ("+", "＋", "-", "－", "减")):
        total_amount = round(sum(amounts), 2)
        note = " ".join(_fmt_signed_amount(amt) for amt in amounts)
        await _do_record(
            message,
            fwd_uid,
            fwd_name,
            total_amount,
            src_hash,
            project_name=project_name,
            amount_note=f"（运算：{note}）",
        )
        return

    if len(amounts) == 1:
        await _do_record(message, fwd_uid, fwd_name, amounts[0], src_hash, project_name=project_name)
    else:
        # Multiple candidates — let the user pick
        context.user_data["pending"] = {
            "fwd_uid": fwd_uid,
            "fwd_name": fwd_name,
            "project_name": project_name,
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
    if not _is_allowed(update):
        await query.edit_message_text("❌ 无权限访问")
        return

    if query.data.startswith("menu:"):
        is_admin = bool(update.effective_user and _is_admin(update.effective_user.id))
        raw_action = query.data.split(":", 1)[1]
        action, action_arg = raw_action, None
        if ":" in raw_action:
            action, action_arg = raw_action.split(":", 1)

        if action == "home":
            _clear_flow(context)
            await query.edit_message_text(
                _menu_text(is_admin),
                parse_mode=ParseMode.HTML,
                reply_markup=_main_menu_keyboard(is_admin),
            )
            return

        if action == "stats":
            _clear_flow(context)
            now = datetime.now(config.TZ)
            if is_admin:
                stats = await get_monthly_stats(now.year, now.month)
                text = _fmt_monthly(now.year, now.month, stats)
            else:
                stats = await get_monthly_stats_for_user(now.year, now.month, update.effective_user.id)
                text = _fmt_monthly_user(now.year, now.month, update.effective_user.id, stats)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_main_menu_keyboard(is_admin),
            )
            return

        if action == "todaystats":
            _clear_flow(context)
            now = datetime.now(config.TZ)
            if is_admin:
                stats = await get_daily_stats(now.date())
                text = _fmt_daily(now.date(), stats)
            else:
                stats = await get_daily_stats_for_user(now.date(), update.effective_user.id)
                text = _fmt_daily_user(now.date(), update.effective_user.id, stats)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_main_menu_keyboard(is_admin),
            )
            return

        if action == "weekstats":
            _clear_flow(context)
            now = datetime.now(config.TZ).date()
            start_date, end_date = _week_bounds(now)
            if is_admin:
                stats = await get_range_stats(start_date, end_date)
                text = _fmt_weekly(start_date, end_date, stats)
            else:
                stats = await get_range_stats_for_user(start_date, end_date, update.effective_user.id)
                text = _fmt_weekly_user(start_date, end_date, update.effective_user.id, stats)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_main_menu_keyboard(is_admin),
            )
            return

        if action == "datestats":
            if not update.effective_chat or update.effective_chat.type != "private":
                await query.edit_message_text("❌ 日期查账请在私聊中使用")
                return
            _clear_flow(context)
            context.user_data["flow_action"] = "stats_date"
            await query.edit_message_text(
                "请输入要查询的日期（如 2026-06-25）",
                reply_markup=_cancel_flow_keyboard(),
            )
            return

        if (
            not update.effective_chat
            or update.effective_chat.type != "private"
            or (
                not is_admin
                and action
                not in {
                    "listprojects",
                    "bindproject",
                    "clearself",
                    "listaliases",
                    "bindid",
                    "delalias",
                    "delproject",
                    "datestats",
                }
            )
        ):
            await query.edit_message_text("❌ 当前操作仅管理员可在私聊中执行")
            return

        if action == "listaliases":
            _clear_flow(context)
            text, list_actions, delete_map = await _build_alias_list_view(
                context,
                is_admin,
                update.effective_user.id,
            )
            context.user_data["alias_delete_map"] = delete_map
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=list_actions,
            )
            return

        if action == "listprojects":
            _clear_flow(context)
            text, list_actions, delete_map = await _build_project_list_view(
                is_admin,
                update.effective_user.id,
            )
            context.user_data["project_delete_map"] = delete_map
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=list_actions,
            )
            return

        if action == "delalias":
            if not action_arg:
                await query.edit_message_text(
                    "❌ 删除入口已过期，请重新打开“查看用户关键词”",
                    reply_markup=_main_menu_keyboard(is_admin),
                )
                return
            delete_map = context.user_data.get("alias_delete_map", {})
            keyword = delete_map.get(action_arg)
            if not keyword:
                await query.edit_message_text(
                    "❌ 删除项已过期，请重新打开“查看用户关键词”",
                    reply_markup=_main_menu_keyboard(is_admin),
                )
                return
            owner_user_id = 0 if is_admin else update.effective_user.id
            removed = await remove_alias(keyword, owner_user_id=owner_user_id)
            text, list_actions, new_map = await _build_alias_list_view(
                context,
                is_admin,
                update.effective_user.id,
            )
            context.user_data["alias_delete_map"] = new_map
            status = (
                f"✅ 已删除用户关键词：<code>{html.escape(keyword)}</code>"
                if removed
                else f"ℹ️ 用户关键词 <code>{html.escape(keyword)}</code> 不存在，已保持现状"
            )
            await query.edit_message_text(
                f"{status}\n\n{text}",
                parse_mode=ParseMode.HTML,
                reply_markup=list_actions,
            )
            return

        if action == "delproject":
            if not action_arg:
                await query.edit_message_text(
                    "❌ 删除入口已过期，请重新打开“查看项目关键词”",
                    reply_markup=_main_menu_keyboard(is_admin),
                )
                return
            delete_map = context.user_data.get("project_delete_map", {})
            keyword = delete_map.get(action_arg)
            if not keyword:
                await query.edit_message_text(
                    "❌ 删除项已过期，请重新打开“查看项目关键词”",
                    reply_markup=_main_menu_keyboard(is_admin),
                )
                return
            owner_user_id = 0 if is_admin else update.effective_user.id
            removed = await remove_project_alias(keyword, owner_user_id=owner_user_id)
            text, list_actions, new_map = await _build_project_list_view(
                is_admin,
                update.effective_user.id,
            )
            context.user_data["project_delete_map"] = new_map
            status = (
                f"✅ 已删除项目关键词：<code>{html.escape(keyword)}</code>"
                if removed
                else f"ℹ️ 项目关键词 <code>{html.escape(keyword)}</code> 不存在，已保持现状"
            )
            await query.edit_message_text(
                f"{status}\n\n{text}",
                parse_mode=ParseMode.HTML,
                reply_markup=list_actions,
            )
            return

        if action == "allowlist":
            _clear_flow(context)
            await query.edit_message_text(
                "📋 <b>当前可用用户列表</b>\n" + await _fmt_allowed_user_ids(context),
                parse_mode=ParseMode.HTML,
                reply_markup=_main_menu_keyboard(True),
            )
            return

        if action == "bindid":
            _clear_flow(context)
            context.user_data["flow_action"] = "bindid_keyword"
            await query.edit_message_text("请输入关键词", reply_markup=_cancel_flow_keyboard())
            return

        if action == "bindproject":
            _clear_flow(context)
            context.user_data["flow_action"] = "bindproject_keyword"
            await query.edit_message_text("请输入关键词", reply_markup=_cancel_flow_keyboard())
            return

        if action == "clearself":
            _clear_flow(context)
            deleted = await clear_entries_by_forward_uid(update.effective_user.id)
            await query.edit_message_text(
                f"✅ 已清空你自己的记账，共删除 <b>{deleted}</b> 条",
                parse_mode=ParseMode.HTML,
                reply_markup=_main_menu_keyboard(False),
            )
            return

        if action == "clearuser":
            _clear_flow(context)
            context.user_data["flow_action"] = "clearuser_uid"
            await query.edit_message_text("请输入要清空的用户ID（整数）", reply_markup=_cancel_flow_keyboard())
            return

        if action == "clearuserproject":
            _clear_flow(context)
            context.user_data["flow_action"] = "clearuserproject_uid"
            await query.edit_message_text("请输入要清空的用户ID（整数）", reply_markup=_cancel_flow_keyboard())
            return

        if action == "allowadd":
            _clear_flow(context)
            context.user_data["flow_action"] = "allow_add"
            await query.edit_message_text("请输入要添加的用户ID（整数）", reply_markup=_cancel_flow_keyboard())
            return

        if action == "allowremove":
            _clear_flow(context)
            context.user_data["flow_action"] = "allow_remove"
            await query.edit_message_text("请输入要删除的用户ID（整数）", reply_markup=_cancel_flow_keyboard())
            return

        if action == "statsuser":
            _clear_flow(context)
            context.user_data["flow_action"] = "stats_user"
            await query.edit_message_text("请输入要查询统计的用户ID（整数）", reply_markup=_cancel_flow_keyboard())
            return

        await query.edit_message_text("❌ 无效操作", reply_markup=_main_menu_keyboard(is_admin))
        return

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
        project_name=pending["project_name"],
        amount=amount,
        chat_id=pending["chat_id"],
        message_id=pending["msg_id"],
        source_hash=src_hash,
    )
    who = pending["fwd_name"] or (str(pending["fwd_uid"]) if pending["fwd_uid"] else "未知")
    if inserted:
        running_total = await get_running_total_for_source(
            forward_uid=pending["fwd_uid"],
            forward_name=pending["fwd_name"],
        )
        await query.edit_message_text(
            f"✅ 已记账\n👤 来源：{who}\n📁 项目：{pending['project_name']}\n💰 金额：{_fmt_signed_amount(amount)}\n🧾 当前累计：{_fmt_signed_amount(running_total)}"
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
    project_name: str,
    amount_note: Optional[str] = None,
) -> None:
    inserted = await insert_entry(
        forward_uid=fwd_uid,
        forward_name=fwd_name,
        project_name=project_name,
        amount=amount,
        chat_id=message.chat_id,
        message_id=message.message_id,
        source_hash=src_hash,
    )
    who = fwd_name or (str(fwd_uid) if fwd_uid else "未知")
    if inserted:
        running_total = await get_running_total_for_source(
            forward_uid=fwd_uid,
            forward_name=fwd_name,
        )
        amount_line = f"💰 金额：{_fmt_signed_amount(amount)}"
        if amount_note:
            amount_line += f"\n🧮 {amount_note}"
        amount_line += f"\n🧾 当前累计：{_fmt_signed_amount(running_total)}"
        await message.reply_text(
            f"✅ 已记账\n👤 来源：{who}\n📁 项目：{project_name}\n{amount_line}"
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
    lines.append("")
    lines.append("📁 分项目明细：")
    if stats.get("projects"):
        for name, total, cnt in stats["projects"]:
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
    lines.append("")
    lines.append("📁 分项目排行：")
    if stats.get("projects"):
        for i, (project, total, cnt) in enumerate(stats["projects"], 1):
            lines.append(f"  {i}. {project}：¥{total:,.2f}（{cnt} 笔）")
    else:
        lines.append("  （无数据）")
    return "\n".join(lines)


def _fmt_monthly_user(year: int, month: int, forward_uid: int, stats: dict) -> str:
    lines = [
        f"📊 <b>{year}年{month}月 用户 {forward_uid} 入账统计</b>",
        f"💰 总额：¥{stats['total']:,.2f}",
        f"📝 笔数：{stats['count']} 笔",
        "",
        "📁 分项目排行：",
    ]
    if stats.get("projects"):
        for i, (project, total, cnt) in enumerate(stats["projects"], 1):
            lines.append(f"  {i}. {project}：¥{total:,.2f}（{cnt} 笔）")
    else:
        lines.append("  （无数据）")
    return "\n".join(lines)


def _fmt_weekly(start_date: date, end_date: date, stats: dict) -> str:
    lines = [
        f"📊 <b>{start_date} ~ {end_date} 本周入账统计</b>",
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
    lines.append("")
    lines.append("📁 分项目排行：")
    if stats.get("projects"):
        for i, (project, total, cnt) in enumerate(stats["projects"], 1):
            lines.append(f"  {i}. {project}：¥{total:,.2f}（{cnt} 笔）")
    else:
        lines.append("  （无数据）")
    return "\n".join(lines)


def _fmt_weekly_user(start_date: date, end_date: date, forward_uid: int, stats: dict) -> str:
    lines = [
        f"📊 <b>{start_date} ~ {end_date} 用户 {forward_uid} 本周入账统计</b>",
        f"💰 总额：¥{stats['total']:,.2f}",
        f"📝 笔数：{stats['count']} 笔",
        "",
        "📁 分项目排行：",
    ]
    if stats.get("projects"):
        for i, (project, total, cnt) in enumerate(stats["projects"], 1):
            lines.append(f"  {i}. {project}：¥{total:,.2f}（{cnt} 笔）")
    else:
        lines.append("  （无数据）")
    return "\n".join(lines)


def _fmt_daily_user(d: object, forward_uid: int, stats: dict) -> str:
    lines = [
        f"📊 <b>{d} 用户 {forward_uid} 入账统计</b>",
        f"💰 总额：¥{stats['total']:,.2f}",
        f"📝 笔数：{stats['count']} 笔",
        "",
        "📁 分项目明细：",
    ]
    if stats.get("projects"):
        for name, total, cnt in stats["projects"]:
            lines.append(f"  • {name}：¥{total:,.2f}（{cnt} 笔）")
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
    global runtime_allowed_user_ids
    await init_db()
    runtime_allowed_user_ids = set(config.ALLOWED_USER_IDS)
    runtime_allowed_user_ids.update(await list_allowed_users())


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.warning(
            "Telegram conflict detected; webhook registration may be overlapping during restart."
        )
        return
    logger.exception("Unhandled Telegram error", exc_info=context.error)


def main() -> None:
    if not config.WEBHOOK_URL:
        raise RuntimeError(
            "WEBHOOK_BASE_URL is required for webhook mode "
            "(or set RAILWAY_PUBLIC_DOMAIN on Railway)."
        )

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("bindid", cmd_bindid))
    app.add_handler(CommandHandler("listaliases", cmd_listaliases))
    app.add_handler(CommandHandler("bindproject", cmd_bindproject))
    app.add_handler(CommandHandler("listprojects", cmd_listprojects))
    app.add_handler(CommandHandler("clearuser", cmd_clearuser))
    app.add_handler(CommandHandler("clearuserproject", cmd_clearuserproject))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Forward message handler (text or caption)
    app.add_handler(
        MessageHandler(
            filters.FORWARDED & (filters.TEXT | filters.CAPTION),
            handle_forward,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED,
            handle_private_text_input,
        )
    )

    # Inline-keyboard callbacks
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(amt|menu):"))
    app.add_error_handler(_on_error)

    # Daily report at 00:00 local time
    midnight = dt_time(0, 0, 0, tzinfo=config.TZ)
    app.job_queue.run_daily(_job_daily, time=midnight)

    logger.info("Bot starting (webhook)…")
    app.run_webhook(
        listen=config.WEBHOOK_LISTEN,
        port=config.WEBHOOK_PORT,
        url_path=config.WEBHOOK_PATH,
        webhook_url=config.WEBHOOK_URL,
        secret_token=config.WEBHOOK_SECRET_TOKEN or None,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
