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


def set_turn_recall_policy(policy: RecallPolicy, scope: str) -> None:
    """调整本轮策略并停止在途准备，不重启检索或重写已有上下文。"""
    turn = _turn.get()
    if turn is None or not turn.active or turn.scope != scope:
        raise ValueError("当前没有可调整的对话轮次")
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
    state = RecallTurn(scope, policy or _override.get() or default_policy or default_recall_policy())
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
    "memory_recall_level": {
        "description": "自动召回强度：off 仅保留固定纪律；tags 精确标签；fast 本地检索；deep 规划与联邦检索。手动 recall 始终可用",
        "default": "deep", "value_type": "enum", "options": ["off", "tags", "fast", "deep"],
    },
    "memory_recall_background": {
        "description": "允许深入档在首轮准备阶段并行深探，返回前等待收尾；执行中不再自动检索",
        "default": True,
    },
}})
