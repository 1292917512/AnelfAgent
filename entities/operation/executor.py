"""操作执行 — 统一入口、执行历史与 MCP 网关。

桌面动作直执行（entities/operation/desktop）；MCP 操作经 entities.mcp
桥调用（get_mcp_bridge 现查单例——桥未初始化/热拔除时返回 None）。
执行历史为进程内环形缓冲（Web 与 AI 共用的可观测面）。

模型可见：MCP 失败沿用统一错误契约，保留结果未知与重试约束。
Token 影响：复用已有回执，不增加注入记录。
缓存影响：仅操作结果与动态历史变化，不修改稳定前缀。
"""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Awaitable, Callable, Dict, List, NamedTuple, Optional

from core.cancellation import ToolCancelled
from core.log import log
from core.tool_results import extract_error_text, parse_tool_result_json

from . import desktop, framework

_LOG_TAG = "操作"

_HISTORY_MAX = 50
_MCP_RESULT_CHARS = 2000


class McpGateway(NamedTuple):
    """MCP 桥的编程调用面。"""

    call: Callable[[str, Dict[str, Any]], Awaitable[str]]
    """按注册工具名调用（返回渲染文本或 tool_error JSON）。"""
    connected_servers: Callable[[], Dict[str, List[str]]]
    """已连接 server → 注册工具名列表。"""
    server_status: Callable[[], List[Dict[str, Any]]]
    """全部配置 server 的状态（name/connected/tool_count/...）。"""


def mcp_gateway() -> Optional[McpGateway]:
    """现查 MCP 桥单例组装网关（桥未初始化/被拔除时返回 None）。"""
    try:
        from entities.mcp.bridge import get_mcp_bridge

        bridge = get_mcp_bridge()
        if bridge is None:
            return None
        return McpGateway(
            call=bridge.call_tool,
            connected_servers=bridge.get_connected_servers,
            server_status=bridge.list_available_servers,
        )
    except Exception:
        return None


_history: deque = deque(maxlen=_HISTORY_MAX)
_last_activity: float = 0.0
"""最近一次操作执行时刻——操作活跃窗口的锚点（态势注入据此按需触发）。"""


def touch_activity() -> None:
    global _last_activity
    _last_activity = time.time()


def seconds_since_activity() -> float:
    """距最近一次操作执行的秒数（从未执行返回 inf）。"""
    return (time.time() - _last_activity) if _last_activity else float("inf")


def _record(op_id: str, kind: str, args: Dict[str, Any], outcome: Dict[str, Any]) -> None:
    _history.append({
        "op": op_id, "kind": kind,
        "args": json.dumps(args, ensure_ascii=False)[:200],
        "ok": bool(outcome.get("ok")),
        "detail": str(outcome.get("result") or outcome.get("error") or "")[:300],
        "ts": time.time(),
        "receipt": {key: outcome[key] for key in (
            "operation_id", "status", "completed", "total", "process_stopped", "input_released",
            "input_outcome", "verification",
        ) if key in outcome},
    })


async def execute(op_id: str, args: Optional[Dict[str, Any]] = None, *,
                  observer: desktop.Observer | None = None) -> Dict[str, Any]:
    """按目录执行一个操作（停用/缺运行时/网关未就绪均返回结构化失败）。"""
    args = args or {}
    spec = framework.get_operation(op_id)
    if spec is None:
        return {"ok": False, "error": f"操作不存在: {op_id}"}
    if not spec.enabled:
        return {"ok": False, "error": f"操作已停用: {op_id}"}

    if spec.kind == framework.KIND_DESKTOP:
        touch_activity()
        try:
            outcome = await desktop.run_action(spec.id.split(".", 1)[1], args, observer)
        except ToolCancelled as exc:
            _record(op_id, spec.kind, args, json.loads(exc.result))
            raise
    else:
        gateway = mcp_gateway()
        if gateway is None:
            outcome = {"ok": False, "error": "MCP 网关未就绪（无 MCP server 或桥未初始化）"}
        else:
            try:
                raw = await gateway.call(spec.tool, args)
                parsed = parse_tool_result_json(raw)
                error = extract_error_text(parsed)
                if error and isinstance(parsed, dict):
                    outcome = {**parsed, "ok": False, "error": error[:_MCP_RESULT_CHARS]}
                else:
                    detail = str(parsed.get("result") or raw) if isinstance(parsed, dict) else raw
                    outcome = {"ok": True, "result": detail[:_MCP_RESULT_CHARS]}
            except Exception as exc:
                outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    _record(op_id, spec.kind, args, outcome)
    touch_activity()
    log(f"操作执行 {op_id}: {'成功' if outcome.get('ok') else outcome.get('error')}",
        "DEBUG", tag=_LOG_TAG)
    return outcome


def history(limit: int = 10) -> List[Dict[str, Any]]:
    """最近执行记录（新在前）。"""
    entries = list(_history)[-max(1, limit):]
    entries.reverse()
    return entries


async def status() -> Dict[str, Any]:
    """运行态：桌面执行器可用性、活跃窗口、MCP servers、操作计数、近期历史。"""
    from core.config import get_config_bool, get_config_int

    specs = framework.list_operations()
    desktop_status = await desktop.get_controller().probe()
    gateway = mcp_gateway()
    servers: List[Dict[str, Any]] = []
    if gateway is not None:
        try:
            servers = gateway.server_status()
        except Exception as exc:
            log(f"MCP 状态获取失败: {exc}", "DEBUG", tag=_LOG_TAG)
    window = max(0, get_config_int("operation_context_window_seconds", 600))
    since = seconds_since_activity()
    return {
        "desktop": {
            **desktop_status,
            "verify": get_config_bool("operation_desktop_verify", True),
            "control": desktop.get_controller().state(),
        },
        "active": {
            "window_seconds": window,
            "seconds_since_activity": None if since == float("inf") else round(since),
            "context_injecting": since <= window,
        },
        "mcp": {"available": gateway is not None, "servers": servers},
        "counts": {
            "operations": len(specs),
            "enabled": sum(1 for s in specs if s.enabled),
            "mcp_registered": sum(1 for s in specs if s.kind == framework.KIND_MCP),
        },
        "history": history(10),
    }
