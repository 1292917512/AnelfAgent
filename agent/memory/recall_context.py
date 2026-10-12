"""每轮自动召回策略的动态提示，不修改前缀或已注入的记忆。

模型可见：档位、精确标签与后台开关；手动召回始终可用。
Token 影响：约 50 tokens；缓存影响：仅 provider 尾部。
"""
from core.context_provider import ContextProviderRegistry, ProviderMeta
from core.tags import tag_label

from .recall_policy import current_recall_policy, has_recall_turn


def recall_policy_context(scope: str) -> str:
    if not has_recall_turn(scope):
        return ""
    policy = current_recall_policy(scope)
    return (tag_label("recall_mode", policy.level)
            + tag_label("recall_background", str(policy.background).lower())
            + "".join(tag_label("recall_tag", tag) for tag in policy.tags)
            + " 自动召回策略；已有记忆不撤回。按需调用 recall；set_recall_policy 可调整本轮后续召回。")


ContextProviderRegistry.register(ProviderMeta(
    name="recall_policy", priority=33, max_tokens=160, group="memory",
    provide_fn=recall_policy_context, description="当前轮次自动召回策略",
))
