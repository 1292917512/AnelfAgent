"""思维工具入口、核心工具注册与内部执行的权限策略。

模型看到：结束回复与手动压缩工具，名称、参数和回执保持既有契约。
Token：工具目录与返回内容不变，不增加上下文消息。
缓存：不改变工具顺序、提示词内容或上下文分层。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agent.mind.tool_activation import ToolActivationManager
from core.entity import EntityRegistry
from core.latebind import LateBinding
from core.log import log
from core.tool_registry import activate_group, deferred_tool

if TYPE_CHECKING:
    from agent.context.compressor import ContextCompressor
    from agent.mind.prefrontal_cortex import PrefrontalCortex

END_REPLY_TOOL_NAME = "end_reply"
OUTPUT_TOOL_NAMES = frozenset({"send_message", "send_photo", "send_voice", "send_file"})
_REFLECT_ALWAYS_BLOCKED = frozenset({"schedule_reply"})

compressor_port: LateBinding[ContextCompressor] = LateBinding("compressor")


@deferred_tool(
    name="compress_context",
    group="thinking", tags=["always"], source="mind.core",
    description="手动触发上下文压缩：将早期对话压缩为摘要以释放上下文空间。"
    "当感觉对话变长、响应变慢或收到上下文溢出提示时使用。"
    "可通过 focus_topic 指定你最关心的话题，压缩时该话题相关信息将被优先完整保留。",
)
def _compress_context_tool(reason: str = "", focus_topic: str = "") -> str:
    """手动触发上下文压缩。

    Args:
        reason: 触发原因（仅日志记录）
        focus_topic: 可选的焦点话题，压缩摘要将优先完整保留该话题相关信息
    """
    compressor = compressor_port.get() if compressor_port.bound else None
    if compressor is None:
        return json.dumps({"error": "压缩器未初始化"}, ensure_ascii=False)
    try:
        scope = ToolActivationManager.current_scope()
    except Exception:
        scope = ""
    compressor.request_manual(scope, focus_topic=focus_topic)
    if reason:
        log(f"AI 请求手动压缩: {reason} (focus={focus_topic or '无'})", tag="压缩")
    return json.dumps({
        "ok": True,
        "message": "压缩请求已登记，将在下一轮 LLM 调用前执行。"
                   + (f"焦点话题「{focus_topic}」的信息将被优先保留。" if focus_topic else ""),
    }, ensure_ascii=False)


@deferred_tool(
    name=END_REPLY_TOOL_NAME,
    group="thinking", tags=["always"], source="mind.core",
    description=(
        "结束本轮操作，不再执行后续工具或生成回复。已回复或无需回复时调用；"
        "reason 及同批正文仅为内部记录，不发给用户。需回复时先调用 send_message 并确认目标会话的成功回执。"
    ),
)
def _end_reply_tool(reason: str = "") -> str:
    """静默结束当前执行，reason 仅作为内部备注。"""
    from agent.channel.delivery import delivery_tags
    from core.conversation_scope import current_scope

    if reason:
        log(f"AI 结束操作: {reason}", tag="思维")
    return json.dumps({"ok": True, "action": "end_reply",
                       "delivery": delivery_tags("silent", "skipped", current_scope())}, ensure_ascii=False)


def register_core_tools() -> None:
    """加载工具声明后激活核心分组，不依赖调用方的导入顺序。"""
    import agent.hooks.tools  # noqa: F401
    import agent.judgment.tools  # noqa: F401
    from agent.mind.tools import (  # noqa: F401
        background,
        execution_log,
        scheduler,
        session_tools,
        short_term_tools,
    )

    activate_group("hooks", "钩子管理 — 声明、配置和执行模式")
    if END_REPLY_TOOL_NAME not in EntityRegistry.get_all_names():
        activate_group("thinking", "思维工具 - 对话流程控制与工具编排")
        log("思维工具已注册 (end_reply)", "DEBUG", tag="思维")
    if "switch_session" not in EntityRegistry.get_all_names():
        count = activate_group("session", "会话管理 — 多频道/多会话的发现与切换")
        if count:
            log(f"会话管理工具已注册 ({count} 个)", "DEBUG", tag="思维")
    if "judge" not in EntityRegistry.get_all_names():
        count = activate_group("judgment", "判断 — 结构化选择/评分/是非评判")
        if count:
            log(f"判断工具已注册 ({count} 个)", "DEBUG", tag="思维")


def build_reflect_blocklist(allow_output_tools: bool) -> set[str]:
    """按内部任务的外发权限生成工具执行黑名单。"""
    blocked = set(_REFLECT_ALWAYS_BLOCKED)
    if not allow_output_tools:
        blocked.update(OUTPUT_TOOL_NAMES)
    return blocked


async def select_reflect_tools(
    pfc: PrefrontalCortex,
    *,
    adapter_key: str,
    scope: str,
    tool_tags: list[str] | None = None,
    inherited_tools: list[dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """按任务选择器装配反思工具，继承已见目录顺序；权限由执行侧单独检查。"""
    from core.config import get_config_bool

    share_reply_tools = get_config_bool("reflect_share_reply_tools", True)
    if tool_tags or not share_reply_tools:
        selectors = tool_tags if tool_tags else ["heartbeat"]
        active_tools = await pfc.get_reflect_tool_schemas(
            adapter_key, scope=scope, selectors=selectors,
        )
    else:
        selectors = []
        active_tools = await pfc.get_active_tool_schemas(adapter_key, scope=scope)

    if inherited_tools is not None:
        from agent.mind.tool_assembly import inherit_tool_catalog
        active_tools = await inherit_tool_catalog(inherited_tools, active_tools, scope=scope)
    return active_tools, selectors
