"""Telegram 命令处理系统 -- /start /help /reset /status 等。

参照 openclaw bot-native-commands.ts。
"""

from __future__ import annotations

from typing import Any, List, Optional

from core.log import log


# 内置命令定义
BUILTIN_COMMANDS = [
    {"command": "start", "description": "开始对话"},
    {"command": "help", "description": "显示帮助信息"},
    {"command": "reset", "description": "重置当前会话"},
    {"command": "status", "description": "查看智能体状态"},
]


async def register_commands(bot: Any) -> None:
    """将命令菜单注册到 Telegram（在聊天输入框显示 / 菜单）。"""
    try:
        from telegram import BotCommand
        commands = [
            BotCommand(command=c["command"], description=c["description"])
            for c in BUILTIN_COMMANDS
        ]
        await bot.set_my_commands(commands)
        log(f"Telegram 已注册 {len(commands)} 个命令菜单")
    except Exception as exc:
        log(f"Telegram 注册命令菜单失败: {exc}", "WARNING")


async def handle_command(
    update: Any,
    context: Any,
    *,
    bot_username: str,
    bot: Any,
) -> bool:
    """处理内置命令。返回 True 表示已处理，False 表示非内置命令需继续走普通消息流。"""
    message = update.effective_message
    if not message or not message.text:
        return False

    text = message.text.strip()
    if not text.startswith("/"):
        return False

    parts = text.split(maxsplit=1)
    raw_cmd = parts[0].lstrip("/").lower()
    if "@" in raw_cmd:
        cmd_name, target_bot = raw_cmd.split("@", 1)
        if bot_username and target_bot != bot_username.lower():
            return False
    else:
        cmd_name = raw_cmd

    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return False

    if cmd_name == "start":
        await _cmd_start(bot, chat_id)
        return True
    if cmd_name == "help":
        await _cmd_help(bot, chat_id)
        return True
    if cmd_name == "reset":
        await _cmd_reset(bot, chat_id, update)
        return True
    if cmd_name == "status":
        await _cmd_status(bot, chat_id)
        return True

    return False


async def _cmd_start(bot: Any, chat_id: int) -> None:
    from .send import send_text
    await send_text(
        bot, chat_id,
        "你好！我是 AnelfAgent 🤖\n\n"
        "你可以直接发消息和我对话，或使用以下命令：\n"
        "/help - 查看帮助\n"
        "/reset - 重置会话\n"
        "/status - 查看状态",
        parse_mode=None,
    )


async def _cmd_help(bot: Any, chat_id: int) -> None:
    from .send import send_text
    lines = ["<b>可用命令</b>\n"]
    for cmd in BUILTIN_COMMANDS:
        lines.append(f"/{cmd['command']} - {cmd['description']}")
    lines.append("\n直接发送消息即可与我对话。群聊中请 @我 来触发回复。")
    await send_text(bot, chat_id, "\n".join(lines))


async def _cmd_reset(bot: Any, chat_id: int, update: Any) -> None:
    from .send import send_text
    user = update.effective_user
    user_id = str(user.id) if user else "unknown"

    try:
        from services._runtime import get_runtime
        rt = get_runtime()
        if rt:
            from agent.storage.storage_router import StorageDomain
            is_group = update.effective_chat.type in ("group", "supergroup")
            if is_group:
                scope_type, scope_id = "group", str(chat_id)
            else:
                scope_type, scope_id = "user", user_id
            await rt.data_center.router.clear(
                StorageDomain.CONVERSATION,
                scope_type=scope_type,
                scope_id=scope_id,
            )
            await send_text(bot, chat_id, "会话已重置 ✅", parse_mode=None)
            log(f"Telegram 会话已重置: {scope_type}:{scope_id}")
        else:
            await send_text(bot, chat_id, "智能体尚未就绪，请稍后再试。", parse_mode=None)
    except Exception as exc:
        log(f"Telegram /reset 失败: {exc}", "WARNING")
        await send_text(bot, chat_id, f"重置失败: {exc}", parse_mode=None)


async def _cmd_status(bot: Any, chat_id: int) -> None:
    from .send import send_text
    try:
        from services._runtime import get_agent_app
        app = get_agent_app()
        if app:
            info = app.get_status_info()
            uptime = info.get("uptime", 0)
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)
            lines = [
                f"<b>状态</b>: {info.get('status', 'unknown').upper()}",
                f"<b>运行时间</b>: {hours}h {minutes}m",
                f"<b>已处理消息</b>: {info.get('message_count', 0)}",
                f"<b>错误次数</b>: {info.get('error_count', 0)}",
                f"<b>队列</b>: {info.get('queue_size', 0)}",
            ]
            await send_text(bot, chat_id, "\n".join(lines))
        else:
            await send_text(bot, chat_id, "智能体尚未就绪。", parse_mode=None)
    except Exception as exc:
        await send_text(bot, chat_id, f"获取状态失败: {exc}", parse_mode=None)
