"""界面交互实体 — AI 操作 Web 工作台界面的内置工具组。

所有命令经 core.event_bus 的 EVENT_UI_COMMAND 事件发出，
由 web 层桥接到 SSE 推送给前端，本模块不依赖 web。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.event_bus import EVENT_UI_COMMAND, event_bus
from entities._sdk import ErrorCause, entity, error_from_exception, tool, tool_error

entity("ui", "界面交互 - 向 Web 工作台投递通知、弹窗提问、切换面板、注入草稿、查询界面状态")

_VALID_LEVELS = {"info", "success", "warning", "error"}
_VALID_PANELS = {"status", "trace", "files", "tasks", "search", "settings"}


@dataclass
class _PendingAsk:
    """挂起的提问：Future + 创建时间（替代对 Future 的猴子补丁）。"""

    future: "asyncio.Future[str]"
    created_at: float


# 挂起的提问：ask_id -> _PendingAsk
_pending_asks: Dict[str, _PendingAsk] = {}
_ASK_MAX_AGE = 600.0

# 前端上报的工作台状态快照
_ui_state: Dict[str, Any] = {}

# _ui_state / _pending_asks 读写锁（异步工具侧使用；
# web 层同步入口 update_ui_state/resolve_ask 均为单次原子字典操作，不加锁）
_state_lock = asyncio.Lock()


def update_ui_state(state: Dict[str, Any]) -> None:
    """web 层调用：更新前端上报的工作台状态快照。"""
    global _ui_state
    new_state = dict(state)
    new_state["updated_at"] = time.time()
    _ui_state = new_state


def get_ui_state_snapshot() -> Dict[str, Any]:
    """web 层调用：读取工作台状态快照。"""
    return dict(_ui_state)


def resolve_ask(ask_id: str, answer: str) -> bool:
    """web 层调用：以用户回答解决挂起的提问，返回是否命中。"""
    pending = _pending_asks.pop(ask_id, None)
    if pending is None or pending.future.done():
        return False
    pending.future.set_result(answer)
    return True


def _cleanup_stale_asks() -> None:
    """清理超龄仍未解决的提问（调用方需持有 _state_lock）。"""
    now = time.time()
    for ask_id, pending in list(_pending_asks.items()):
        if now - pending.created_at > _ASK_MAX_AGE and not pending.future.done():
            pending.future.cancel()
            _pending_asks.pop(ask_id, None)


async def _emit(command: str, payload: Dict[str, Any]) -> None:
    """发出界面命令事件。"""
    await event_bus.emit(EVENT_UI_COMMAND, {"command": command, **payload})


@tool(name="ui_notify", group="ui", tags=["always"])
async def ui_notify(title: str, content: str = "", level: str = "info") -> str:
    """向 Web 工作台投递一条通知卡片（任务完成、发现异常、进度提醒等）。

    Args:
        title: 通知标题
        content: 通知正文（可选，支持简短说明）
        level: 级别 info/success/warning/error
    """
    try:
        normalized = level.strip().lower()
        if normalized not in _VALID_LEVELS:
            normalized = "info"
        await _emit("notify", {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "content": content,
            "level": normalized,
            "ts": time.time(),
        })
        return json.dumps({"success": True}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="投递界面通知")


@tool(name="ui_ask", group="ui", tags=["always"], timeout=630.0)
async def ui_ask(question: str, options: Optional[List[str]] = None, timeout: int = 120) -> str:
    """向 Web 工作台弹窗提问并等待用户回答（选项式或自由输入）。无用户在线时会超时。

    Args:
        question: 要问用户的问题
        options: 可选的回答选项列表，为空则用户自由输入
        timeout: 等待回答的超时秒数（最大 600）
    """
    try:
        async with _state_lock:
            _cleanup_stale_asks()
            ask_id = uuid.uuid4().hex[:12]
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            _pending_asks[ask_id] = _PendingAsk(future=future, created_at=time.time())

        await _emit("ask", {
            "ask_id": ask_id,
            "question": question,
            "options": options or [],
            "ts": time.time(),
        })
        try:
            answer = await asyncio.wait_for(future, timeout=min(max(timeout, 5), 600))
            return json.dumps({"success": True, "answer": answer}, ensure_ascii=False)
        except asyncio.TimeoutError:
            async with _state_lock:
                _pending_asks.pop(ask_id, None)
            return json.dumps({"timeout": True, "answer": ""}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="弹窗提问")


@tool(name="ui_open_panel", group="ui", tags=["always"])
async def ui_open_panel(panel: str, payload: str = "") -> str:
    """打开 Web 工作台右侧面板并可附带定位内容（如打开文件、填入搜索词）。

    Args:
        panel: 面板名 status/trace/files/tasks/search/settings
        payload: 可选参数：files 面板为文件路径，search 面板为搜索词
    """
    try:
        normalized = panel.strip().lower()
        if normalized not in _VALID_PANELS:
            return tool_error(f"未知面板: {panel}，可选: {sorted(_VALID_PANELS)}",
                              cause=ErrorCause.PARAM, retryable=False)
        await _emit("open_panel", {"panel": normalized, "payload": payload})
        return json.dumps({"success": True, "panel": normalized}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="打开界面面板")


@tool(name="ui_compose", group="ui", tags=["always"])
async def ui_compose(text: str) -> str:
    """向 Web 工作台对话输入框注入草稿（由用户确认后发送，不会自动发送）。

    Args:
        text: 要预填到输入框的文本
    """
    try:
        if not text.strip():
            return tool_error("草稿内容为空", cause=ErrorCause.PARAM, retryable=False)
        await _emit("compose", {"text": text, "ts": time.time()})
        return json.dumps({"success": True}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="注入对话草稿")


@tool(name="ui_get_state", concurrency_safe=True, group="ui", tags=["always"])
async def ui_get_state() -> str:
    """获取 Web 工作台界面状态快照（当前面板、打开的文件、输入框草稿等）。"""
    try:
        async with _state_lock:
            snapshot = dict(_ui_state)
        if not snapshot:
            return json.dumps({"available": False, "hint": "前端尚未上报状态"}, ensure_ascii=False)
        return json.dumps({"available": True, "state": snapshot}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="获取界面状态")
