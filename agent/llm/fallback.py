"""按调用的模型类型控制失败后是否尝试下一候选。

模型看到：失败时仅已启用切换的能力会向下一候选发送同一请求。
Token：首选请求用量不变，关闭切换可避免额外模型调用。
缓存：策略在路由时读取，不注入消息或改动提示词前缀。
"""

from agent.llm.config import ModelType
from core.config import get_config_bool, register_configs_safe


def _key(kind: ModelType) -> str:
    return f"model_fallback_{kind.value}"


def fallback_enabled(kind: ModelType) -> bool:
    """读取当前类型的跨模型切换策略，只有对话默认开启。"""
    return get_config_bool(_key(kind), kind is ModelType.CHAT)


register_configs_safe({
    "models/fallback": {
        _key(kind): {
            "description": f"{kind.value} 首选模型调用失败后，按优先级尝试下一候选",
            "default": kind is ModelType.CHAT,
            "tag": kind.value,
        }
        for kind in ModelType
    },
})
