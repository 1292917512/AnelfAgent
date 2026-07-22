"""界面交互实体 — AI 操作 Web 工作台界面的内置工具组。

所有命令经 core.event_bus 的 EVENT_UI_COMMAND 事件发出，
由 web 层桥接到 SSE 推送给前端，本模块不依赖 web。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from core.event_bus import EVENT_UI_COMMAND, event_bus
from core.log import log
from entities._sdk import entity, tool

entity("ui", "界面交互 - 向 Web 工作台投递通知、弹窗提问、切换面板、注入草稿、查询界面状态")

_VALID_LEVELS = {"info", "success", "warning", "error"}
_VALID_PANELS = {"status", "trace", "files", "tasks", "search", "settings"}

# 挂起的提问：ask_id -> (Future, 创建时间)
_pending_asks: Dict[str, asyncio.Future[str]] = {}
_ASK_MAX_AGE = 600.0

# 前端上报的工作台状态快照
_ui_state: Dict[str, Any] = {}


def update_ui_state(state: Dict[str, Any]) -> None:
    """web 层调用：更新前端上报的工作台状态快照。"""
    global _ui_state
    _ui_state = dict(state)
    _ui_state["updated_at"] = time.time()


def get_ui_state_snapshot() -> Dict[str, Any]:
    """web 层调用：读取工作台状态快照。"""
    return dict(_ui_state)


def resolve_ask(ask_id: str, answer: str) -> bool:
    """web 层调用：以用户回答解决挂起的提问，返回是否命中。"""
    future = _pending_asks.pop(ask_id, None)
    if future is None or future.done():
        return False
    future.set_result(answer)
    return True


def _cleanup_stale_asks() -> None:
    """清理超龄仍未解决的提问。"""
    now = time.time()
    # future 附带创建时间属性
    for ask_id, future in list(_pending_asks.items()):
        created = getattr(future, "_created_at", now)
        if now - created > _ASK_MAX_AGE and not future.done():
            future.cancel()
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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="ui_ask", group="ui", tags=["always"], timeout=630.0)
async def ui_ask(question: str, options: Optional[List[str]] = None, timeout: int = 120) -> str:
    """向 Web 工作台弹窗提问并等待用户回答（选项式或自由输入）。无用户在线时会超时。

    Args:
        question: 要问用户的问题
        options: 可选的回答选项列表，为空则用户自由输入
        timeout: 等待回答的超时秒数（最大 600）
    """
    try:
        _cleanup_stale_asks()
        ask_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        future._created_at = time.time()  # type: ignore[attr-defined]
        _pending_asks[ask_id] = future

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
            _pending_asks.pop(ask_id, None)
            return json.dumps({"timeout": True, "answer": ""}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
            return json.dumps(
                {"error": f"未知面板: {panel}，可选: {sorted(_VALID_PANELS)}"},
                ensure_ascii=False,
            )
        await _emit("open_panel", {"panel": normalized, "payload": payload})
        return json.dumps({"success": True, "panel": normalized}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="ui_compose", group="ui", tags=["always"])
async def ui_compose(text: str) -> str:
    """向 Web 工作台对话输入框注入草稿（由用户确认后发送，不会自动发送）。

    Args:
        text: 要预填到输入框的文本
    """
    try:
        if not text.strip():
            return json.dumps({"error": "草稿内容为空"}, ensure_ascii=False)
        await _emit("compose", {"text": text, "ts": time.time()})
        return json.dumps({"success": True}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="ui_get_state", group="ui", tags=["always"])
async def ui_get_state() -> str:
    """获取 Web 工作台界面状态快照（当前面板、打开的文件、输入框草稿等）。"""
    try:
        if not _ui_state:
            return json.dumps({"available": False, "hint": "前端尚未上报状态"}, ensure_ascii=False)
        return json.dumps({"available": True, "state": _ui_state}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
