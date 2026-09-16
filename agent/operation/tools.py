"""操作 AI 工具面 — 桌面操控、MCP 操作注册与语义化执行。

桌面动作为统一入口 desktop_act（动作枚举 + 可选参数，参数契约见
list_operations 的目录描述）；注册的 MCP 操作经 execute_operation
按注释语义调用。看屏定位用视觉组既有工具（vision_look），此处不重复。
"""

from __future__ import annotations

import json

from core.log import log
from entities._sdk import ErrorCause, deferred_tool, tool_error

from . import executor, framework

_LOG_TAG = "操作"


def _runtime_gate() -> str:
    """桌面执行器总开关（停用时动作拒绝执行）。"""
    from core.config import get_config_bool

    if not get_config_bool("operation_enabled", True):
        return tool_error(
            "桌面操控已停用（operation_enabled）",
            cause=ErrorCause.STATE, retryable=False,
            hint="确需操作时请先在配置中心开启 operation_enabled",
        )
    return ""


@deferred_tool(
    group="operation", tags=["always"], source="agent.operation",
    description="执行桌面操控动作（click/double_click/right_click/move/drag/scroll/type/"
    "hotkey/key）。先用 vision_look 看屏定位坐标。",
)
async def desktop_act(
    action: str,
    x: int = 0,
    y: int = 0,
    x2: int = 0,
    y2: int = 0,
    text: str = "",
    keys: str = "",
    amount: int = 0,
    button: str = "left",
    duration: float = 0.0,
    interval: float = 0.0,
) -> str:
    """执行一个桌面操控动作。

    Args:
        action: click/double_click/right_click/move/drag/scroll/type/hotkey/key
        x/y: 目标坐标（click/dblclick/rightclick/move/drag 起点）
        x2/y2: drag 终点坐标
        text: type 动作的文本（仅 ASCII，中文走剪贴板+hotkey 粘贴）
        keys: hotkey 组合（"ctrl+s"）或 key 单键（"enter"）
        amount: scroll 滚动格数（正上负下）
        button: 鼠标键（left/right/middle）
        duration: move/drag 滑动时长秒
        interval: type 逐字间隔秒
    """
    gate = _runtime_gate()
    if gate:
        return gate
    known = {a.id.split(".", 1)[1] for a in framework.DESKTOP_ACTIONS}
    if action not in known:
        return tool_error(
            f"未知动作 {action}（可选: {', '.join(sorted(known))}）",
            cause=ErrorCause.PARAM, retryable=False,
        )
    spec = framework.get_operation(f"desktop.{action}")
    if spec is not None and not spec.enabled:
        return tool_error(f"该动作已被停用: {action}", cause=ErrorCause.STATE, retryable=False)
    outcome = await executor.execute(f"desktop.{action}", {
        "x": x, "y": y, "x2": x2, "y2": y2, "text": text, "keys": keys,
        "amount": amount, "button": button, "duration": duration, "interval": interval,
    })
    return json.dumps(outcome, ensure_ascii=False)


@deferred_tool(
    group="operation", tags=["always"], source="agent.operation",
    description="列出全部操作（内置桌面动作 + 注册的 MCP 操作），含注释与参数说明。",
)
def list_operations() -> str:
    """列出操作目录（id/类型/说明/注释/启停）。"""
    specs = framework.list_operations()
    lines = []
    for s in specs:
        state = "启用" if s.enabled else "停用"
        note = f"｜注: {s.annotation}" if s.annotation else ""
        server = f"｜server: {s.server}" if s.kind == framework.KIND_MCP else ""
        lines.append(
            f"- {s.id}（{s.kind}/{state}）{s.title}: {s.description[:120]}{server}{note}"
        )
    return "\n".join(lines) or "（无操作）"


@deferred_tool(
    group="operation", tags=["always"], source="agent.operation",
    description="执行一个注册的 MCP 操作（按 list_operations 的注释语义调用）。",
)
async def execute_operation(op_id: str, args_json: str = "{}") -> str:
    """执行注册的 MCP 操作。

    Args:
        op_id: 操作 id（mcp.{server}.{工具名}，见 list_operations）
        args_json: 工具参数 JSON 对象字符串（参数 schema 见操作目录）
    """
    try:
        args = json.loads(args_json) if args_json and args_json.strip() else {}
        if not isinstance(args, dict):
            raise ValueError("args_json 必须是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as exc:
        return tool_error(
            f"args_json 解析失败: {exc}", cause=ErrorCause.PARAM, retryable=False,
        )
    outcome = await executor.execute(op_id, args)
    return json.dumps(outcome, ensure_ascii=False)


@deferred_tool(
    group="operation", source="agent.operation",
    description="把一个 MCP 工具注册为一等操作（可加语义注释，注入上下文供后续调用）。",
)
def register_mcp_operation(server: str, tool: str, note: str = "") -> str:
    """注册 MCP 工具为操作。

    Args:
        server: MCP server 名（须已连接）
        tool: 工具名（operation_status 可查已连接 server 的工具清单）
        note: 语义注释（如"打开网页"）
    """
    gateway = executor.mcp_gateway()
    if gateway is None:
        return tool_error(
            "MCP 网关未就绪（无 MCP server 或桥未初始化）",
            cause=ErrorCause.STATE, retryable=False,
        )
    connected = gateway.connected_servers()
    if server not in connected:
        return tool_error(
            f"server 未连接: {server}（已连接: {', '.join(connected) or '无'}）",
            cause=ErrorCause.STATE, retryable=False,
        )
    registered = connected[server]
    matched = next((t for t in registered if t == tool or t.rsplit("__", 1)[-1] == tool), "")
    if not matched:
        return tool_error(
            f"工具不存在: {tool}（该 server 共 {len(registered)} 个工具，"
            "operation_status 可查全清单）",
            cause=ErrorCause.PARAM, retryable=False,
        )
    description, params = _tool_meta(matched)
    spec = framework.register_mcp_operation(
        server=server, tool=matched, annotation=note,
        description=description, params=params,
    )
    log(f"MCP 操作已注册: {spec.id}", "INFO", tag=_LOG_TAG)
    return json.dumps({"ok": True, "op_id": spec.id, "params": params}, ensure_ascii=False)


@deferred_tool(group="operation", source="agent.operation")
def remove_operation(op_id: str) -> str:
    """移除一个注册的 MCP 操作（内置桌面动作不可删）。"""
    ok = framework.remove_operation(op_id)
    return json.dumps({"ok": ok, "error": "" if ok else "操作不存在或不可删除"},
                      ensure_ascii=False)


@deferred_tool(group="operation", source="agent.operation")
def update_operation(op_id: str, note: str = "", enabled: bool = True) -> str:
    """更新操作的注释与启停（note 空串清除注释）。"""
    spec = framework.get_operation(op_id)
    if spec is None:
        return json.dumps({"ok": False, "error": f"操作不存在: {op_id}"}, ensure_ascii=False)
    framework.set_annotation(op_id, note)
    framework.set_enabled(op_id, enabled)
    return json.dumps({"ok": True, "op_id": op_id, "note": note, "enabled": enabled},
                      ensure_ascii=False)


@deferred_tool(
    group="operation", tags=["always"], source="agent.operation",
    description="操作运行态：桌面执行器可用性、MCP servers 连接与工具清单、最近执行历史。",
)
async def operation_status() -> str:
    """查询操作运行态与已连接 MCP server 的工具清单。"""
    status = await executor.status()
    gateway = executor.mcp_gateway()
    if gateway is not None:
        try:
            status["mcp"]["tools"] = gateway.connected_servers()
        except Exception:
            pass
    return json.dumps(status, ensure_ascii=False)


def _tool_meta(registered_name: str) -> tuple:
    """从实体注册表取工具描述与参数 schema（注册快照用）。"""
    try:
        from core.entity import EntityRegistry, EntityType

        for meta in EntityRegistry.get_by_type(EntityType.TOOL):
            if meta.name == registered_name:
                params = list((meta.meta or {}).get("params") or [])
                return meta.description or "", params
    except Exception as exc:
        log(f"工具元数据获取失败: {exc}", "DEBUG", tag=_LOG_TAG)
    return "", []
