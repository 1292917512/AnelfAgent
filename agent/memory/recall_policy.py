"""自动召回的全局默认、输入覆盖与单轮策略。

模型可见：策略工具返回生效档位；召回结果沿用实体与记忆标签。
Token 影响：关闭/标签/快速档减少自动检索，手动 recall 不受限制。
缓存影响：策略不进入 stable 层，首轮深探快照仅经 provider 尾部注入。
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.config import get_config, get_config_bool, register_configs_safe

RecallLevel = Literal["off", "tags", "fast", "deep"]


class RecallPolicy(BaseModel):
    """单轮自动召回策略；tags 是精确交集，不改变手动工具权限。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    level: RecallLevel = "deep"
    tags: tuple[str, ...] = Field(default=(), max_length=12)
    background: bool = True

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tag.strip() or len(tag) > 160 for tag in tags):
            raise ValueError("标签不能为空且每个最多 160 字符")
        return tuple(dict.fromkeys(tag.strip() for tag in tags))

    @model_validator(mode="after")
    def validate_level_tags(self) -> RecallPolicy:
        if self.tags and self.level not in {"tags", "fast"}:
            raise ValueError("指定精确标签时请选择 tags 或 fast，避免深入检索超出指定范围")
        return self


@dataclass
class RecallTurn:
    scope: str
    policy: RecallPolicy
    active: bool = True
    adaptive: bool = False


_override: ContextVar[RecallPolicy | None] = ContextVar("recall_override", default=None)
_turn: ContextVar[RecallTurn | None] = ContextVar("recall_turn", default=None)


def default_recall_policy() -> RecallPolicy:
    return RecallPolicy.model_validate({
        "level": get_config("memory_recall_level", "deep"),
        "background": get_config_bool("memory_recall_background", True),
    })


def current_recall_policy(scope: str = "") -> RecallPolicy:
    turn = _turn.get()
    if turn is not None and turn.active and turn.scope == scope:
        return turn.policy
    return _override.get() or default_recall_policy()


def has_recall_turn(scope: str) -> bool:
    state = _turn.get()
    return bool(state is not None and state.active and state.scope == scope)


def can_adapt_recall(scope: str) -> bool:
    state = _turn.get()
    return bool(state is not None and state.active and state.scope == scope and state.adaptive
                and state.policy.level == "deep" and get_config_bool("memory_recall_adaptive_enabled", True))


def apply_adaptive_recall(scope: str, policy: RecallPolicy) -> None:
    """准备开始前选择一次强度，不覆盖消息、SDK 或频道显式约定。"""
    if can_adapt_recall(scope):
        state = _turn.get()
        assert state is not None
        state.policy, state.adaptive = policy, False


def set_turn_recall_policy(policy: RecallPolicy, scope: str) -> None:
    """调整本轮策略并停止在途准备，不重启检索或重写已有上下文。"""
    turn = _turn.get()
    if turn is None or not turn.active or turn.scope != scope:
        raise ValueError("当前没有可调整的对话轮次")
    turn.adaptive = False
    if turn.policy == policy:
        return
    turn.policy = policy
    from .probe import deep_probe_hub
    deep_probe_hub.stop(scope)


@contextmanager
def recall_options(policy: RecallPolicy) -> Iterator[None]:
    """为 SDK 直接调用的回复入口提供隔离的策略覆盖。"""
    token = _override.set(policy)
    try:
        yield
    finally:
        _override.reset(token)


@asynccontextmanager
async def recall_turn(
    scope: str, policy: RecallPolicy | None = None, *, default_policy: RecallPolicy | None = None,
) -> AsyncIterator[RecallTurn]:
    """按消息、SDK、入口默认、全局顺序选择策略，结束时回收本轮召回。"""
    from .probe import deep_probe_hub
    explicit = policy or _override.get() or default_policy
    state = RecallTurn(scope, explicit or default_recall_policy(), adaptive=explicit is None)
    token = _turn.set(state)
    probe = deep_probe_hub.begin_reply(scope, managed=True)
    try:
        yield state
    finally:
        state.active = False
        try:
            await deep_probe_hub.end_reply(scope, expected=probe)
        finally:
            _turn.reset(token)


register_configs_safe({"memory/recall": {
    "memory_query_rewrite_enabled": {
        "description": "召回前是否用轻量 LLM 改写检索查询（口语→检索友好形式）",
        "default": True,
    },
    "memory_recall_timeout_seconds": {
        "description": "首轮可选准备总预算：分级、向量、检索、技能补充、关联和重排共享；到期保留已有证据",
        "default": 5.0,
        "advanced": True,
        "unit": "秒",
    },
    "memory_plan_budget_seconds": {
        "description": "检索规划（轻量 LLM 产出多查询计划）的独立超时预算，超时回退原查询单发；0 = 按召回总超时的 40% 份额派生（召回路径中至多占总超时减 1 秒，保证检索段时间）",
        "default": 0.0,
        "advanced": True,
        "unit": "秒",
    },
    "memory_recall_permanent_pin": {
        "description": "永久记忆置顶注入条数（0 = 关闭；教导/规则类每轮固定注入）",
        "default": 3,
        "advanced": True,
        "unit": "条",
    },
    "memory_forgotten_recall_limit": {
        "description": "每次召回最多附带展示的已遗忘记忆条数（归档 + 墓碑合并）",
        "default": 3,
        "advanced": True,
        "unit": "条",
    },
    "memory_archive_recall_min_score": {
        "description": "归档记忆兜底召回的最低相关度（低于此分的归档与全部墓碑仅在主检索无果时出现）",
        "default": 0.5,
        "advanced": True,
        "value_type": "range",
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    "memory_recall_adaptive_enabled": {
        "description": "自动按本轮需求选择召回强度：Jev 高置信时选择不检索或本地检索，不确定仍深入召回；显式消息、SDK 和频道策略优先",
        "default": True,
    },
    "memory_recall_route_timeout": {
        "description": "召回分级的原生判断总预算；不可用或超时沿用深入召回，不调用普通模型补判",
        "default": 1.0, "min": 0.1, "max": 5.0, "unit": "秒", "advanced": True,
    },
    "memory_recall_route_confidence": {
        "description": "减少检索所需的最低判断概率：不检索须确信无需历史事实，快速检索须确信无需历史关联；有歧义保留深入检索",
        "default": 0.9, "min": 0.8, "max": 1.0, "advanced": True,
    },
    "memory_recall_level": {
        "description": "自动召回强度：off 仅保留固定纪律；tags 精确标签；fast 本地检索；deep 规划与联邦检索。手动 recall 始终可用",
        "default": "deep", "value_type": "enum", "options": ["off", "tags", "fast", "deep"],
    },
    "memory_recall_background": {
        "description": "允许首轮并行深探及新增对话的后台补充；工具输出不触发自动检索",
        "default": True,
    },
    "memory_dialogue_refresh_limit": {
        "description": "同一回复内新增对话触发补充召回的最大批次；0 关闭。只检索本地记忆和技能，不等待或唤醒主模型",
        "default": 3, "min": 0, "max": 10, "advanced": True,
    },
    "memory_dialogue_refresh_timeout": {
        "description": "每批对话补充召回的后台总预算，含查询向量、检索与可选判断；超时不影响主流程",
        "default": 5.0, "min": 0.1, "max": 30.0, "unit": "秒", "advanced": True,
    },
}})
