"""hooks_llm 管理服务 — web 侧薄门面（LLM 钩子面的注册表观测与运行状态）。

钩子的注册/变更由代码（@llm_hook 装饰器）与任务/实体入口完成，Web 面板
只做观测与治理参数展示：列出全部已注册钩子、上下文档位、触发统计与治理
配置。钩子的开关与治理参数经统一配置面（hooks_llm/* 组）热调，不在此另设
写路径。
"""

from __future__ import annotations

from typing import Any, Dict, List


class HooksLlmService:
    """LLM 钩子面观测门面（注册表 + 运行时状态只读）。"""

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
                "tool_tags": list(spec.tool_tags),
            })
        return {
            "enabled": get_config_bool("hooks_llm_enabled", True),
            "runtime_started": bool(runtime and runtime._started),
            "events": sorted(HOOK_EVENTS),
            "hooks": hooks,
            "governance": {
                "max_concurrent": get_config_int("hooks_llm_max_concurrent", 2),
                "transcript_enabled": get_config_bool("hooks_llm_transcript_enabled", True),
                "transcript_max_chars": get_config_int("hooks_llm_transcript_max_chars", 60000),
            },
        }


hooks_llm_service = HooksLlmService()
