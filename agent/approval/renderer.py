"""批准渲染器 — 各频道渲染抽象。

BaseChannel 提供默认文本渲染；子类可覆盖为：
- Telegram: InlineKeyboard 按钮
- WebUI: 原生按钮 + JSON 折叠
- CLI: y/n 提示
- QQ: 富文本 + 关键词回复
- Feishu: 卡片消息
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.channel.schemas import SendSegment
from agent.channel.base import ApprovalPromptRenderContext


def render_default_approval_text(ctx: ApprovalPromptRenderContext) -> str:
    """默认文本渲染（所有频道兜底）。"""
    risk_emoji = {
        "low": "ℹ️",
        "medium": "⚠️",
        "high": "🔶",
        "critical": "🚨",
    }.get(ctx.risk_level.lower(), "⚠️")

    return (
        f"{risk_emoji} **工具调用需要批准**\n"
        f"\n"
        f"工具: `{ctx.tool_name}`\n"
        f"参数: ```\n{ctx.tool_args_summary}\n```\n"
        f"风险等级: **{ctx.risk_level}**\n"
        f"原因: {ctx.reason}\n"
        f"超时: {ctx.timeout_seconds}s\n"
        f"\n"
        f"请回复以下命令之一：\n"
        f"  `approve {ctx.request_id}` — 允许执行\n"
        f"  `deny {ctx.request_id}` — 拒绝执行\n"
    )


def render_telegram_approval(ctx: ApprovalPromptRenderContext) -> Dict[str, Any]:
    """Telegram 渲染：文本 + InlineKeyboard。"""
    text = render_default_approval_text(ctx)
    return {
        "text": text,
        "parse_mode": "markdown",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ 允许", "callback_data": f"approve:{ctx.request_id}"},
                    {"text": "❌ 拒绝", "callback_data": f"deny:{ctx.request_id}"},
                ],
            ],
        },
    }


def render_webui_approval(ctx: ApprovalPromptRenderContext) -> Dict[str, Any]:
    """WebUI 渲染：富文本 + 按钮事件。"""
    return {
        "type": "approval_request",
        "request_id": ctx.request_id,
        "tool_name": ctx.tool_name,
        "tool_args_summary": ctx.tool_args_summary,
        "risk_level": ctx.risk_level,
        "reason": ctx.reason,
        "timeout_seconds": ctx.timeout_seconds,
        "actions": [
            {"label": "允许", "action": "approve", "style": "primary"},
            {"label": "拒绝", "action": "deny", "style": "danger"},
        ],
    }


def render_cli_approval(ctx: ApprovalPromptRenderContext) -> str:
    """CLI 渲染：简洁 y/n 提示。"""
    return (
        f"\n⚠️  工具调用需要批准\n"
        f"  工具: {ctx.tool_name}\n"
        f"  参数: {ctx.tool_args_summary[:200]}\n"
        f"  风险: {ctx.risk_level}\n"
        f"  原因: {ctx.reason}\n"
        f"\n"
        f"输入 'y' 允许, 'n' 拒绝, 超时 {ctx.timeout_seconds}s\n"
        f"[request_id: {ctx.request_id}] "
    )


def parse_approval_command(text: str) -> Optional[tuple[str, str]]:
    """解析批准命令。

    支持格式：
      approve <request_id>     deny <request_id>
      approve:<request_id>     deny:<request_id>   （Telegram 按钮回调格式）
      allow / reject / yes / y / no / n（别名）

    Returns:
        (decision, request_id) 或 None
    """
    text = text.strip()
    # 冒号格式（Telegram InlineKeyboard 回调）
    if ":" in text and " " not in text:
        cmd, _, request_id = text.partition(":")
        cmd_lower = cmd.lower()
        if cmd_lower in ("approve", "allow"):
            return ("approved", request_id.strip())
        if cmd_lower in ("deny", "reject"):
            return ("denied", request_id.strip())
        return None
    parts = text.split(None, 1)
    if len(parts) != 2:
        return None
    cmd, request_id = parts
    cmd_lower = cmd.lower()
    if cmd_lower in ("approve", "allow", "yes", "y"):
        return ("approved", request_id)
    if cmd_lower in ("deny", "reject", "no", "n"):
        return ("denied", request_id)
    return None
