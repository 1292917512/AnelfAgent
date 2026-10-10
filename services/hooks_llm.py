"""hooks_llm 管理服务 — web 侧薄门面（LLM 钩子面的注册表观测与运行状态）。

钩子的注册/变更由代码（@llm_hook 装饰器）与任务/实体入口完成；Web 面板
提供观测与运行期启停（set_enabled 写注册表内存态，重启即恢复代码声明的
初始值）。钩子的开关与治理参数经统一配置面（/api/config/meta，hooks_llm/*
组）热调，本路由只提供面板观测数据，不另设写路径。
"""

from __future__ import annotations

from typing import Any, Dict, List


class HooksLlmService:
    """LLM 钩子面观测门面（注册表 + 运行时状态）。"""

    @staticmethod
    def get_overview() -> Dict[str, Any]:
        """钩子面总览：启用状态 + 全部钩子 + 治理配置。"""
        from agent.hooks_llm import HOOK_EVENTS, HookRegistry, get_hook_runtime
        from core.config import get_config_bool, get_config_int

        runtime = get_hook_runtime()
        hooks: List[Dict[str, Any]] = []
        for spec in HookRegistry.list_all():
            hooks.append({
                "name": spec.name,
                "event": spec.event,
                "context": spec.context.value,
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
            })
        return {
            "enabled": get_config_bool("hooks_llm_enabled", True),
            "runtime_started": bool(runtime and runtime.started),
            "events": sorted(HOOK_EVENTS),
            "hooks": hooks,
            "governance": {
                "max_concurrent": get_config_int("hooks_llm_max_concurrent", 2),
                "transcript_enabled": get_config_bool("hooks_llm_transcript_enabled", True),
                "transcript_max_chars": get_config_int("hooks_llm_transcript_max_chars", 60000),
            },
        }

    @staticmethod
    def set_enabled(name: str, enabled: bool) -> Dict[str, Any]:
        """运行期启停指定钩子（注册表内存态，热生效；重启恢复代码声明初始值）。

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
