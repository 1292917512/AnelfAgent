"""操作执行 — 统一入口、执行历史与 MCP 网关端口。

桌面动作直执行（agent/operation/desktop）；MCP 操作经 McpGateway 端口
调用桥（端口由组合根施绑为"运行时取单例"的工厂——MCP 后初始化/热拔除
都安全）。执行历史为进程内环形缓冲（Web 与 AI 共用的可观测面）。
"""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Awaitable, Callable, Dict, List, NamedTuple, Optional

from core.latebind import LateBinding
from core.log import log

from . import desktop, framework

_LOG_TAG = "操作"

_HISTORY_MAX = 50
_MCP_RESULT_CHARS = 2000


class McpGateway(NamedTuple):
    """MCP 桥的编程调用面（组合根施绑）。"""

    call: Callable[[str, Dict[str, Any]], Awaitable[str]]
    """按注册工具名调用（返回渲染文本或 tool_error JSON）。"""
    connected_servers: Callable[[], Dict[str, List[str]]]
    """已连接 server → 注册工具名列表。"""
    server_status: Callable[[], List[Dict[str, Any]]]
    """全部配置 server 的状态（name/connected/tool_count/...）。"""


operation_mcp_port: LateBinding[Callable[[], Optional[McpGateway]]] = LateBinding(
    "operation.mcp_gateway",
)
"""MCP 网关工厂（每次取用现查单例——桥未初始化/被拔除时返回 None）。"""


def mcp_gateway() -> Optional[McpGateway]:
    if not operation_mcp_port.bound:
        return None
    try:
        return operation_mcp_port.get()()
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
    })


async def execute(op_id: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按目录执行一个操作（停用/缺运行时/网关未就绪均返回结构化失败）。"""
    args = args or {}
    spec = framework.get_operation(op_id)
    if spec is None:
        return {"ok": False, "error": f"操作不存在: {op_id}"}
    if not spec.enabled:
        return {"ok": False, "error": f"操作已停用: {op_id}"}

    if spec.kind == framework.KIND_DESKTOP:
        outcome = await desktop.run_action(spec.id.split(".", 1)[1], args)
    else:
        gateway = mcp_gateway()
        if gateway is None:
            outcome = {"ok": False, "error": "MCP 网关未就绪（无 MCP server 或桥未初始化）"}
        else:
            try:
                raw = await gateway.call(spec.tool, args)
                try:
                    parsed = json.loads(raw)
                    ok = parsed.get("success") is not False
                    detail = str(parsed.get("error") or parsed.get("result") or raw)
                except (json.JSONDecodeError, TypeError):
                    ok, detail = True, raw
                outcome = {"ok": ok, ("result" if ok else "error"): detail[:_MCP_RESULT_CHARS]}
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
    desktop_ready = desktop.runtime_ready()
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
            "available": desktop_ready,
            "screen": list(desktop.screen_size()) if desktop_ready else None,
            "verify": get_config_bool("operation_desktop_verify", True),
            "hint": "" if desktop_ready else desktop.install_hint(),
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
