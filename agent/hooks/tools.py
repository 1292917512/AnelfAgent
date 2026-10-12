"""AI 钩子管理工具；配置与 Web 同源，结果只在工具响应中出现。"""

from __future__ import annotations

import json
from typing import Any

from core.sanitizer import sanitize_text
from core.tool_errors import ErrorCause, tool_error
from core.tool_registry import deferred_tool


@deferred_tool(name="get_hooks", group="hooks", source="mind.hooks",
               description="查看脚本和 LLM 钩子声明及配置键。LLM 参数经 update_entity_config 热更；召回默认见 memory/recall，单轮用 set_recall_policy。")
async def get_hooks() -> str:
    """查询钩子声明，不返回其他会话的执行内容或改写上下文前缀。"""
    from agent.hooks.configuration import hook_configuration
    from agent.hooks_llm.settings import config_keys
    from agent.hooks_llm.spec import HookRegistry
    scripts = hook_configuration.get_hooks()
    return sanitize_text(json.dumps({
        "scripts": scripts["hooks"], "script_events": scripts["events"],
        "llm": [{"name": spec.name, "event": spec.event, "enabled": spec.enabled,
                 "mode": spec.mode.value, "description": spec.description,
                 "config_keys": config_keys(spec)} for spec in HookRegistry.list_all()],
        "hint": "set_script_hooks 修改一个事件的脚本；update_entity_config 修改 LLM 配置键。运行情况用 check_background_tasks，终止用 terminate_background_task。",
    }, ensure_ascii=False))


@deferred_tool(name="set_script_hooks", group="hooks", source="mind.hooks", risk="CRITICAL",
               schema_extra={"hooks": {"items": {"type": "object", "properties": {
                   "command": {"type": "string"}, "matcher": {"type": "string"},
                   "mode": {"type": "string", "enum": ["sync", "async"]},
                   "timeout": {"type": "number", "minimum": 1, "maximum": 60},
                   "enabled": {"type": "boolean"}}, "required": ["command"], "additionalProperties": False}}},
               description="替换指定事件的脚本钩子（tool_pre/tool_post/reply_end），其他事件保持不变。hooks 数组含 command、matcher、mode(sync/async)、timeout、enabled；空数组移除该事件钩子。")
async def set_script_hooks(event: str, hooks: list[dict[str, Any]]) -> str:
    """校验并原子保存单事件脚本声明，成功后立即热重载。"""
    from agent.hooks.configuration import hook_configuration
    from agent.hooks.runner import HOOK_EVENTS
    if event not in HOOK_EVENTS:
        return tool_error(f"未知钩子事件: {event}，可选 {', '.join(HOOK_EVENTS)}", cause=ErrorCause.PARAM, retryable=False)
    try:
        result = hook_configuration.replace_event(event, hooks)
        return json.dumps({**result, "event": event}, ensure_ascii=False)
    except (ValueError, OSError) as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
