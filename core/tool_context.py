"""工具请求归属及受控执行器的停止代次；不参与模型提示词或业务决策。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4


@dataclass(frozen=True)
class ToolRequest:
    """一个请求及其后台子任务共享的来源和创建时控制代次。"""

    request_id: str
    scope: str
    actor: str
    epochs: dict[str, int]


@dataclass
class _Control:
    world: str
    barriers: frozenset[str]
    epoch: int = 0
    delegates: dict[str, str] = field(default_factory=dict)


_producer = uuid4().hex
_lock = RLock()
_controls: dict[str, _Control] = {}
_request: ContextVar[ToolRequest | None] = ContextVar("tool_request", default=None)


def register_control(server: str, world: str, barriers: frozenset[str]) -> None:
    """声明需要停止代次的执行器；重复注册保留代次。"""
    with _lock:
        state = _controls.get(server)
        if state is None:
            _controls[server] = _Control(world, barriers)
        else:
            if state.world != world:
                state.epoch += 1
            state.world, state.barriers = world, barriers


@contextmanager
def tool_request(scope: str = "", actor: str = "", *, inherit: bool = False) -> Iterator[ToolRequest]:
    """后台任务继承发起请求的代次，不能在停止后获取新代次继续旧任务。"""
    existing = _request.get()
    if inherit and existing is not None:
        yield existing
        return
    with _lock:
        epochs = {name: state.epoch for name, state in _controls.items()}
    origin = ToolRequest(uuid4().hex, scope, actor, epochs)
    token = _request.set(origin)
    try:
        yield origin
    finally:
        _request.reset(token)


def control_metadata(server: str, tool: str, delegation_id: str = "") -> dict[str, Any] | None:
    """生成 MCP 私有元数据；停止提升代次，旧请求仍携带原代次供执行器拒绝。"""
    with _lock:
        state = _controls.get(server)
        if state is None:
            return None
        origin = _request.get()
        epoch = origin.epochs.get(server, -1) if origin else state.epoch
        if tool in state.barriers and epoch == state.epoch and (origin is None or origin.actor != "@reflex"):
            state.epoch += 1
            epoch = state.epoch
        if delegation_id:
            state.delegates[delegation_id] = origin.scope if origin else ""
        return {"anelf/action": {
            "producer": _producer, "epoch": epoch, "floor": state.epoch,
            "requestId": origin.request_id if origin else uuid4().hex,
            "scope": origin.scope if origin else "", "actor": origin.actor if origin else "",
            "delegationId": delegation_id, "worldId": state.world,
        }}


def refresh_control_metadata(server: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """重连重试保留请求原代次，仅刷新撤销水位，避免新执行器接受过期请求。"""
    with _lock:
        state = _controls.get(server)
        if state is None:
            return metadata
        return {"anelf/action": {**metadata["anelf/action"], "floor": state.epoch}}


def is_control_current(server: str, producer: str, epoch: int) -> bool:
    """只接受当前宿主代次的执行事件，过期缓冲事件不能重新打开自主动作。"""
    with _lock:
        state = _controls.get(server)
        return state is not None and producer == _producer and epoch == state.epoch


def consume_control_delegates(server: str) -> dict[str, str]:
    """取走曾调用该执行器的委托归属，供宿主按真实委托 ID 取消。"""
    with _lock:
        state = _controls.get(server)
        if state is None:
            return {}
        delegates, state.delegates = state.delegates, {}
        return delegates


def forget_control_delegate(delegation_id: str) -> None:
    """委托结束后移除归属，避免历史记录无限累积或误中断同会话后续任务。"""
    with _lock:
        for state in _controls.values():
            state.delegates.pop(delegation_id, None)
