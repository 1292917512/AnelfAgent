"""hooks_llm 配置注册（统一配置面，AI 经 get/update_entity_config 热调）。"""
from __future__ import annotations

from core.config import register_configs_safe

_HOOKS_LLM_CONFIGS = {
    "hooks_llm/core": {
        "hooks_llm_enabled": {
            "description": "是否启用 LLM 钩子面（对话/思考边界派生异步 LLM 工作的统一注册面）",
            "default": True,
        },
        "hooks_llm_max_concurrent": {
            "description": "钩子全局并发上限（所有钩子共享的总量护栏，单钩子另有自身 max_concurrent）",
            "default": 2,
            "advanced": True,
            "unit": "个",
        },
        "hooks_llm_transcript_enabled": {
            "description": "是否允许钩子继承完整 transcript 快照（关闭则 transcript 档位降级为无快照，防大上下文成本）",
            "default": True,
        },
    },
}

register_configs_safe(_HOOKS_LLM_CONFIGS)
