"""judgment 配置注册（统一配置面，AI 经 get/update_entity_config 热调）。"""
from __future__ import annotations

from core.config import register_configs_safe

_JUDGMENT_CONFIGS = {
    "judgment/core": {
        "judgment_enabled": {
            "description": "是否启用判断能力（关闭后 judge 工具从目录隐藏，引擎调用直接拒绝）",
            "default": True,
        },
        "judgment_api_key": {
            "description": "TypeSafe API Key（留空时走普通模型回退通道；支持 ${ENV_VAR} 外置）",
            "default": "",
            "value_type": "password",
        },
        "judgment_base_url": {
            "description": "TypeSafe API 地址",
            "default": "https://api.typesafe.ai",
            "advanced": True,
        },
        "judgment_model": {
            "description": "TypeSafe 模型版本（System One 模型，如 jev-latest）",
            "default": "jev-latest",
        },
        "judgment_timeout": {
            "description": "单次判断调用的超时时间（每条通道独立计时）",
            "default": 30.0,
            "value_type": "range",
            "min": 5.0,
            "max": 120.0,
            "unit": "秒",
        },
        "judgment_fallback_enabled": {
            "description": "未配置密钥或原生通道失败时，是否回退到普通聊天模型完成判断",
            "default": True,
        },
        "judgment_fallback_model": {
            "description": "回退判断专用模型 ID（留空走默认聊天模型链；判断是轻量任务，建议小快模型）",
            "default": "",
        },
        "judgment_fallback_effort": {
            "description": "回退判断的思考档位（模型不支持思考时自动忽略）",
            "default": "low",
            "advanced": True,
        },
        "judgment_context_messages": {
            "description": "judge context_mode=conversation 时注入的当前会话最近消息条数",
            "default": 6,
            "value_type": "range",
            "min": 1,
            "max": 50,
            "unit": "条",
        },
        "judgment_context_max_chars": {
            "description": "conversation 档注入上下文的字符护栏（超出从最早的消息截断，保留最新）",
            "default": 4000,
            "advanced": True,
            "unit": "字符",
        },
        "judgment_full_max_chars": {
            "description": "full 档注入上下文的字符护栏（窗口全量消息 + 折叠摘要共用）",
            "default": 20000,
            "advanced": True,
            "unit": "字符",
        },
    },
}

register_configs_safe(_JUDGMENT_CONFIGS)
