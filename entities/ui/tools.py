"""界面交互实体 — AI 操作 Web 工作台界面的内置工具组。

所有命令经 core.event_bus 的 EVENT_UI_COMMAND 事件发出，
由 web 层桥接到 SSE 推送给前端，本模块不依赖 web。

Model Experience：模型经工具结果看到页面在线/后台/空闲状态与未回答归因，未回答不等于授权。
Token 影响：状态与错误结果增加约几十 token，无人值守时避免无效等待与重复提问。
缓存影响：只进入当前工具结果的动态区，不改变缓存前缀。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.event_bus import EVENT_UI_COMMAND, event_bus
from core.log import log
from entities._sdk import ErrorCause, entity, error_from_exception, tool, tool_error
from entities.ui.presence import get_presence, update_state

entity("ui", "界面交互 - 向 Web 工作台投递通知、弹窗提问、切换面板、注入草稿、查询界面状态")

_VALID_LEVELS = {"info", "success", "warning", "error"}
_VALID_PANELS = {"status", "trace", "chat", "context", "files", "tasks", "search", "settings"}


@dataclass
class _PendingAsk:
    """挂起的提问：Future + 创建时间（替代对 Future 的猴子补丁）。"""

    future: "asyncio.Future[str]"
    created_at: float
    expires_at: float


# 挂起的提问：ask_id -> _PendingAsk
_pending_asks: Dict[str, _PendingAsk] = {}
_ASK_MAX_AGE = 600.0

# 挂起提问的异步写入锁；同步入口均在同一事件循环内完成。
_state_lock = asyncio.Lock()


def update_ui_state(state: Dict[str, Any]) -> None:
    """web 层调用：更新前端上报的工作台状态快照。"""
    update_state(state)


def get_ui_state_snapshot() -> Dict[str, Any]:
    """web 层调用：读取工作台状态快照。"""
    return get_presence().get("state", {})


def resolve_ask(ask_id: str, answer: str) -> bool:
    """web 层调用：以用户回答解决挂起的提问，返回是否命中。"""
    pending = _pending_asks.pop(ask_id, None)
    if pending is None or pending.future.done() or time.monotonic() >= pending.expires_at:
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
    """向有近期操作的前台 Web 工作台提问；离线、后台或久未操作时直接返回未回答。

    Args:
        question: 要问用户的问题
        options: 可选的回答选项列表，为空则用户自由输入
        timeout: 等待回答的超时秒数（最大 600）
    """
    try:
        presence = get_presence()
        if not presence["can_ask"]:
            return _unavailable(presence["status"])
        wait_seconds = min(max(timeout, 5), 600)
        async with _state_lock:
            _cleanup_stale_asks()
            ask_id = uuid.uuid4().hex[:12]
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            deadline = time.monotonic() + wait_seconds
            _pending_asks[ask_id] = _PendingAsk(
                future=future, created_at=time.time(), expires_at=deadline,
            )

        try:
            await _emit("ask", {
                "ask_id": ask_id,
                "question": question,
                "options": options or [],
                "ts": time.time(),
                "expires_at": time.time() + wait_seconds,
            })
            while not future.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait({future}, timeout=min(5.0, remaining))
                if not future.done() and not get_presence()["available"]:
                    return _unavailable("offline")
            answer = future.result()
            if answer == "__skipped__":
                return tool_error("用户跳过了问题", cause=ErrorCause.USER_CANCEL, retryable=False)
            return json.dumps({"success": True, "answer": answer}, ensure_ascii=False)
        except asyncio.TimeoutError:
            return tool_error("等待用户回答超时", cause=ErrorCause.TIMEOUT, retryable=False,
                              hint="本次未获得回答，不得视为同意；可继续不依赖回答的工作")
        finally:
            async with _state_lock:
                _pending_asks.pop(ask_id, None)
            if not future.done():
                future.cancel()
            try:
                await _emit("ask_closed", {"ask_id": ask_id})
            except Exception as exc:
                log(f"同步界面提问结束状态失败: {exc}", "DEBUG")
    except Exception as e:
        return error_from_exception(e, action="弹窗提问")


def _unavailable(status: str) -> str:
    """未检测到可交互页面时，返回可归因的非授权结果。"""
    return tool_error(
        "工作台离线或未检测到近期前台操作，本次未获得回答", cause=ErrorCause.STATE,
        retryable=False, ui_status=status,
        hint="页面在场信号不代表有人阅读。不得视为同意；继续不依赖回答的工作，待用户返回后再提问",
    )


@tool(name="ui_open_panel", group="ui", tags=["always"])
async def ui_open_panel(panel: str, payload: str = "") -> str:
    """打开 Web 工作台右侧面板并可附带定位内容（如打开文件、填入搜索词）。

    Args:
        panel: 面板名 status/trace/chat/context/files/tasks/search/settings；trace 打开全局执行区，chat 打开独立 Web 对话窗
        payload: files 为工作区相对路径；项目路径加 project:，目录再加 dir:（如 project:dir:src）；search 为搜索词
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
    """获取页面在线、前台与近期操作信号及界面快照；can_ask 不代表用户已阅读或同意。"""
    try:
        return json.dumps(get_presence(), ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="获取界面状态")
