"""LLM 钩子声明与配置门面，写入复用统一配置持久化纪律。"""

from __future__ import annotations

from typing import Any, Dict, List


class HooksLlmService:
    """LLM 钩子面观测门面（注册表 + 运行时状态）。"""

    @staticmethod
    def get_overview() -> Dict[str, Any]:
        """钩子面总览：启用状态 + 全部钩子 + 治理配置。"""
        from agent.hooks_llm import HOOK_EVENTS, HookRegistry, get_hook_runtime
        from agent.hooks_llm.settings import config_keys
        from core.config import get_config_bool, get_config_int

        runtime = get_hook_runtime()
        hooks: List[Dict[str, Any]] = []
        for spec in HookRegistry.list_all():
            hooks.append({
                "name": spec.name,
                "event": spec.event,
                "context": spec.context.value,
                "mode": spec.mode.value,
                "description": spec.description,
                "owner": spec.owner,
                "source": spec.source,
                "priority": spec.priority,
                "max_iterations": spec.max_iterations,
                "max_concurrent": spec.max_concurrent,
                "cooldown_seconds": spec.cooldown_seconds,
                "debounce_seconds": spec.debounce_seconds,
                "model": spec.model,
                "allow_output_tools": spec.allow_output_tools,
                "route_output": spec.route_output,
                "tool_tags": list(spec.tool_tags),
                "enabled": spec.enabled,
                "config_group": f"hooks_llm/{spec.name}",
                "config_keys": config_keys(spec),
            })
        return {
            "enabled": get_config_bool("hooks_llm_enabled", True),
            "runtime_started": bool(runtime and runtime.started),
            "events": sorted(HOOK_EVENTS),
            "hooks": hooks,
            "governance": {
                "max_concurrent": get_config_int("hooks_llm_max_concurrent", 2),
                "transcript_enabled": get_config_bool("hooks_llm_transcript_enabled", True),
            },
        }

    @staticmethod
    def set_enabled(name: str, enabled: bool) -> Dict[str, Any]:
        """保存指定钩子的启停配置，对后续调度热生效。

        返回更新后的钩子摘要；未找到抛 ValueError。
        """
        from agent.hooks_llm import HookRegistry

        if not HookRegistry.set_enabled(name, enabled):
            raise ValueError(f"钩子不存在: {name}")
        spec = HookRegistry.get(name)
        if spec is None:
            raise ValueError(f"钩子不存在: {name}")
        return {
            "updated": True,
            "hook": {
                "name": spec.name,
                "event": spec.event,
                "enabled": spec.enabled,
                "source": spec.source,
            },
        }


hooks_llm_service = HooksLlmService()
