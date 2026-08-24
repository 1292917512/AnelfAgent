"""MCP 管理工具组（mcp_manage）：让 AI 能自主管理 MCP server。

注册 list/get/update/set/add/remove/connect/disconnect/toggle/reload/template
等管理工具到 EntityRegistry；配置读写直接使用 entities.mcp.config 的
MCPServerStore（entities/mcp 内部单向依赖，不经 services）。
"""

from __future__ import annotations

import functools
import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from core.entity import EntityRegistry, ToolParam
from core.log import log
from core.sanitizer import is_sanitize_enabled, sanitize_text
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import coerce_bool_arg
from entities.mcp.bridge import MCPBridge, get_mcp_bridge
from entities.mcp.config import MCPServerStore

# 工具结果 JSON 中返回的工具名列表上限
_TOOL_RESULT_LIST_LIMIT = 50


def _trigger_bridge_reload() -> None:
    """触发 MCP Bridge 配置热重载（静默失败）。"""
    try:
        bridge = get_mcp_bridge()
        if bridge:
            bridge.reload_config()
    except Exception as e:
        log(f"MCP 配置热重载失败: {e}", "WARNING")


def _new_store() -> MCPServerStore:
    """构造挂接 bridge 热重载的配置存取实例。"""
    return MCPServerStore(on_reload=_trigger_bridge_reload)


def _safe_json(payload: Any) -> str:
    """序列化为 JSON 并脱敏，防止配置中的密钥进入 LLM 上下文与供应商日志。"""
    text = json.dumps(payload, ensure_ascii=False)
    return sanitize_text(text) if is_sanitize_enabled() else text


def register_mcp_tools() -> None:
    """注册 MCP 管理工具到 EntityRegistry。"""
    bridge = get_mcp_bridge()
    server_names = [s.name for s in bridge.config.servers] if bridge else []
    names_hint = f" ({', '.join(server_names)})" if server_names else ""
    EntityRegistry.register_group("mcp_manage", f"MCP 管理 - 查看/连接/断开/增删改 MCP 服务器{names_hint}")

    EntityRegistry.register_tool(
        name="list_mcp_servers",
        func=_tool_list_mcp_servers,
        description="列出所有可用的 MCP 服务器及其连接状态和工具数量。",
        group="mcp_manage",
        params=[],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="get_mcp_server_config",
        func=_tool_get_mcp_server_config,
        description=(
            "读取 MCP 配置。可查看单个 server 配置或完整 mcpServers，"
            "并返回可编辑字段说明，便于后续精准修改。"
        ),
        group="mcp_manage",
        params=[
            ToolParam(name="server_name", description="服务器名称；留空返回完整 mcpServers 配置", type="string", required=False),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="update_mcp_server_config",
        func=_tool_update_mcp_server_config,
        description=(
            "按补丁更新 MCP server 配置（支持 merge/replace、删除字段、可选创建、可选热重载）。"
            "patch_json 传 JSON 对象字符串，例如 {\"transport\":\"streamable_http\",\"timeout\":10}。"
        ),
        group="mcp_manage",
        params=[
            ToolParam(name="server_name", description="服务器名称", type="string", required=True),
            ToolParam(name="patch_json", description="JSON 对象字符串，填写要变更的字段", type="string", required=True),
            ToolParam(name="replace", description="true=整配置替换；false=增量合并（默认）", type="boolean", required=False),
            ToolParam(name="remove_fields", description="要删除的字段列表（逗号分隔或 JSON 数组字符串）", type="string", required=False),
            ToolParam(name="create_if_missing", description="服务器不存在时是否创建", type="boolean", required=False),
            ToolParam(name="reload", description="修改后是否立即热重载", type="boolean", required=False),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="set_mcp_server_enabled",
        func=_tool_set_mcp_server_enabled,
        description="显式设置 MCP server 的 enabled 状态（区别于 toggle，不依赖当前状态猜测）。",
        group="mcp_manage",
        params=[
            ToolParam(name="server_name", description="服务器名称", type="string", required=True),
            ToolParam(name="enabled", description="是否启用", type="boolean", required=True),
            ToolParam(name="reload", description="是否立即热重载", type="boolean", required=False),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="get_mcp_config_template",
        func=_tool_get_mcp_config_template,
        description="返回 MCP server 配置字段模板与示例，便于 AI 构造 update_mcp_server_config 的 patch_json。",
        group="mcp_manage",
        params=[],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="connect_mcp_server",
        func=_tool_connect_mcp_server,
        description="连接指定的 MCP 服务器，连接后其工具可供使用。",
        group="mcp_manage",
        params=[
            ToolParam(name="server_name", description="MCP 服务器名称（通过 list_mcp_servers 获取）", type="string", required=True),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="disconnect_mcp_server",
        func=_tool_disconnect_mcp_server,
        description="断开指定的 MCP 服务器，释放连接和相关工具。",
        group="mcp_manage",
        params=[
            ToolParam(name="server_name", description="MCP 服务器名称（通过 list_mcp_servers 获取）", type="string", required=True),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="toggle_mcp_server",
        func=_tool_toggle_mcp_server,
        description="自动判断 MCP 服务器当前状态并切换：已连接则断开，未连接则连接。",
        group="mcp_manage",
        params=[
            ToolParam(name="server_name", description="MCP 服务器名称（通过 list_mcp_servers 获取）", type="string", required=True),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="add_mcp_server",
        func=_tool_add_mcp_server,
        description=(
            "添加新的 MCP 服务器并热重载。支持 stdio（command）和 HTTP/SSE（url）方式，"
            "并可直接配置 headers/timeout/call_timeout 等字段。"
        ),
        group="mcp_manage",
        params=[
            ToolParam(name="name", description="服务器名称（唯一标识）", type="string", required=True),
            ToolParam(name="url", description="服务器 URL（HTTP/SSE 方式，与 command 二选一）", type="string", required=False),
            ToolParam(name="command", description="启动命令（stdio 方式，与 url 二选一）", type="string", required=False),
            ToolParam(name="args", description="命令参数列表（stdio 方式，JSON 数组字符串）", type="string", required=False),
            ToolParam(name="env", description="环境变量（JSON 对象字符串）", type="string", required=False),
            ToolParam(name="headers", description="HTTP 请求头（JSON 对象字符串）", type="string", required=False),
            ToolParam(name="transport", description="传输方式：stdio / streamable_http / sse（留空自动推断）", type="string", required=False),
            ToolParam(name="enabled", description="是否启用（默认 true）", type="boolean", required=False),
            ToolParam(name="timeout", description="连接超时秒数（>0）", type="number", required=False),
            ToolParam(name="sse_read_timeout", description="SSE 读取超时秒数（>0）", type="number", required=False),
            ToolParam(name="call_timeout", description="工具调用超时秒数（>0）", type="number", required=False),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="remove_mcp_server",
        func=_tool_remove_mcp_server,
        description="删除 MCP 服务器：断开连接并从配置文件移除。",
        group="mcp_manage",
        params=[
            ToolParam(name="server_name", description="要删除的 MCP 服务器名称", type="string", required=True),
        ],
        source="mcp", tags=["core"],
    )

    EntityRegistry.register_tool(
        name="reload_mcp_config",
        func=_tool_reload_mcp_config,
        description="重新从配置文件加载 MCP 服务器配置，自动处理新增/删除/变更的服务器（热重载）。",
        group="mcp_manage",
        params=[],
        source="mcp", tags=["core"],
    )

    log(
        "MCP 管理工具已注册 (list/get/update/set/add/remove/connect/disconnect/toggle/reload/template)",
        tag="思维",
    )


# ------------------------------------------------------------------
# 工具装饰器与公共启停实现
# ------------------------------------------------------------------


def _tool_error_json(exc: Exception) -> str:
    """工具异常统一序列化为归因明确的错误 JSON。"""
    return error_from_exception(exc)


def mcp_tool_call(require_bridge: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """MCP 管理工具装饰器：统一处理 Bridge 可用性检查与异常→错误 JSON。

    require_bridge=True 时校验全局 MCPBridge 已初始化（未初始化返回错误 JSON），
    并以 bridge 关键字参数注入被装饰函数；业务函数只需处理参数与调用逻辑。
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> str:
                if require_bridge:
                    bridge = get_mcp_bridge()
                    if not bridge:
                        return tool_error("MCP Bridge 未初始化", cause=ErrorCause.STATE,
                                          retryable=False, hint="请先在配置中启用并连接 MCP server")
                    kwargs["bridge"] = bridge
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    return _tool_error_json(exc)
            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> str:
            if require_bridge:
                bridge = get_mcp_bridge()
                if not bridge:
                    return tool_error("MCP Bridge 未初始化", cause=ErrorCause.STATE,
                                      retryable=False, hint="请先在配置中启用并连接 MCP server")
                kwargs["bridge"] = bridge
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                return _tool_error_json(exc)
        return sync_wrapper
    return decorator


async def _sync_enabled_flag(server_name: str, enabled: bool, action: str) -> None:
    """连接/断开后同步配置文件的 enabled 状态（失败仅记日志，不影响主流程）。"""
    import asyncio
    try:
        store = _new_store()
        await asyncio.to_thread(store.set_server_enabled, server_name, enabled, reload=False)
    except Exception as inner_exc:
        log(f"同步 enabled 状态失败({action}): {inner_exc}", "DEBUG", tag="mcp")


async def _do_connect_server(bridge: MCPBridge, server_name: str, action: str) -> Dict[str, Any]:
    """连接 MCP server 并同步 enabled=True（connect/toggle 工具共用的启停实现）。"""
    import asyncio
    count = await asyncio.to_thread(bridge.connect_server_by_name, server_name)
    await _sync_enabled_flag(server_name, True, action)
    return {
        "success": True,
        "server": server_name,
        "tools_discovered": count,
        "enabled": True,
    }


async def _do_disconnect_server(bridge: MCPBridge, server_name: str, action: str) -> Dict[str, Any]:
    """断开 MCP server 并同步 enabled=False（disconnect/toggle 工具共用的启停实现）。"""
    import asyncio
    await asyncio.to_thread(bridge.disconnect_server_by_name, server_name)
    await _sync_enabled_flag(server_name, False, action)
    return {
        "success": True,
        "server": server_name,
        "action": "disconnected",
        "enabled": False,
    }


@mcp_tool_call(require_bridge=True)
def _tool_list_mcp_servers(bridge: MCPBridge) -> str:
    servers = bridge.list_available_servers()
    return json.dumps({"servers": servers, "total": len(servers)}, ensure_ascii=False)


@mcp_tool_call()
def _tool_get_mcp_server_config(server_name: str = "") -> str:
    """查看 MCP 原始配置（单个或全部，输出已脱敏）。"""
    store = _new_store()
    schema = store.get_server_config_schema()
    if server_name.strip():
        cfg = store.get_server_config(server_name.strip())
        if cfg is None:
            return json.dumps({
                "error": f"服务器 '{server_name}' 不存在",
                "hint": "可先调用 list_mcp_servers 查看名称",
            }, ensure_ascii=False)
        return _safe_json({
            "server": server_name.strip(),
            "config": cfg,
            "editable_schema": schema,
        })

    full = store.load_config()
    return _safe_json({
        "mcpServers": full.get("mcpServers", {}),
        "editable_schema": schema,
    })


def _parse_remove_fields_arg(remove_fields: str) -> List[str]:
    """解析 remove_fields：支持逗号分隔或 JSON 数组字符串。"""
    raw = (remove_fields or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            log("_parse_remove_fields_arg 异常已忽略", "DEBUG")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _coerce_positive_float_arg(value: Any, field_name: str) -> Optional[float]:
    """解析可选正数参数；空值或 0 视为未提供（返回 None）。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    num = float(value)
    if num == 0:
        return None
    if num < 0:
        raise ValueError(f"{field_name} 必须 > 0")
    return num


@mcp_tool_call()
async def _tool_update_mcp_server_config(
    server_name: str,
    patch_json: str,
    replace: bool = False,
    remove_fields: str = "",
    create_if_missing: bool = False,
    reload: bool = True,
) -> str:
    """按补丁更新 server 配置。"""
    import asyncio

    try:
        patch = json.loads(patch_json or "{}")
    except json.JSONDecodeError as exc:
        return error_from_exception(exc, action="解析 patch_json",
                                    hint="请传入合法 JSON 对象字符串")
    if not isinstance(patch, dict):
        return tool_error("patch_json 必须是 JSON 对象字符串",
                          cause=ErrorCause.PARAM, retryable=False)

    store = _new_store()
    result = await asyncio.to_thread(
        store.update_server_config,
        server_name.strip(),
        patch,
        replace=coerce_bool_arg(replace, False),
        remove_fields=_parse_remove_fields_arg(remove_fields),
        create_if_missing=coerce_bool_arg(create_if_missing, False),
        reload=coerce_bool_arg(reload, True),
    )
    bridge = get_mcp_bridge()
    connected = False
    tools: List[str] = []
    if bridge:
        connected_map = bridge.get_connected_servers()
        tools = connected_map.get(server_name.strip(), [])
        connected = server_name.strip() in connected_map
    return _safe_json({
        "success": True,
        **result,
        "connected": connected,
        "tool_count": len(tools),
        "tools": tools[:_TOOL_RESULT_LIST_LIMIT],
    })


@mcp_tool_call()
async def _tool_set_mcp_server_enabled(
    server_name: str,
    enabled: bool,
    reload: bool = True,
) -> str:
    """显式设置 enabled 状态。"""
    import asyncio

    store = _new_store()
    result = await asyncio.to_thread(
        store.set_server_enabled,
        server_name.strip(),
        coerce_bool_arg(enabled, False),
        reload=coerce_bool_arg(reload, True),
    )
    bridge = get_mcp_bridge()
    connected = False
    if bridge:
        connected = server_name.strip() in bridge.get_connected_servers()
    return json.dumps({
        "success": True,
        **result,
        "connected": connected,
    }, ensure_ascii=False)


@mcp_tool_call()
def _tool_get_mcp_config_template() -> str:
    """返回 MCP 配置模板与 patch 示例。"""
    schema = MCPServerStore.get_server_config_schema()
    return json.dumps({
        "schema": schema,
        "examples": {
            "set_http_server": {
                "server_name": "my-http-server",
                "patch_json": json.dumps({
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer xxx"},
                    "transport": "streamable_http",
                    "enabled": True,
                    "call_timeout": 180,
                }, ensure_ascii=False),
            },
            "set_stdio_server": {
                "server_name": "my-stdio-server",
                "patch_json": json.dumps({
                    "command": "npx",
                    "args": ["-y", "@example/mcp-server"],
                    "env": {"API_KEY": "xxx"},
                    "transport": "stdio",
                }, ensure_ascii=False),
            },
            "remove_fields": {
                "server_name": "my-http-server",
                "patch_json": "{}",
                "remove_fields": "headers,timeout",
            },
        },
        "notes": [
            "update_mcp_server_config 是推荐入口，支持 merge/replace + remove_fields + reload",
            "enabled 建议用 set_mcp_server_enabled 显式控制，避免 toggle 带来的状态不确定",
        ],
    }, ensure_ascii=False)


@mcp_tool_call(require_bridge=True)
async def _tool_connect_mcp_server(server_name: str, bridge: MCPBridge) -> str:
    """异步连接 MCP 服务器，不阻塞 Mind 思考循环。"""
    result = await _do_connect_server(bridge, server_name, "connect")
    return json.dumps(result, ensure_ascii=False)


@mcp_tool_call(require_bridge=True)
async def _tool_disconnect_mcp_server(server_name: str, bridge: MCPBridge) -> str:
    """异步断开 MCP 服务器。"""
    if server_name not in bridge.get_connected_servers():
        return json.dumps({"error": f"服务器 '{server_name}' 未连接"}, ensure_ascii=False)
    result = await _do_disconnect_server(bridge, server_name, "disconnect")
    return json.dumps(result, ensure_ascii=False)


@mcp_tool_call(require_bridge=True)
async def _tool_toggle_mcp_server(server_name: str, bridge: MCPBridge) -> str:
    """自动判断当前状态并切换 MCP 服务器的连接。"""
    if server_name in bridge.get_connected_servers():
        result = await _do_disconnect_server(bridge, server_name, "toggle->disconnect")
    else:
        result = await _do_connect_server(bridge, server_name, "toggle->connect")
        result["action"] = "connected"
    return json.dumps(result, ensure_ascii=False)


@mcp_tool_call(require_bridge=True)
async def _tool_add_mcp_server(
    name: str,
    url: str = "",
    command: str = "",
    args: str = "",
    env: str = "",
    headers: str = "",
    transport: str = "",
    enabled: bool = True,
    timeout: float = 0.0,
    sse_read_timeout: float = 0.0,
    call_timeout: float = 0.0,
    bridge: Optional[MCPBridge] = None,
) -> str:
    """添加 MCP 服务器到配置文件并触发热重载。"""
    import asyncio
    if not url and not command:
        return json.dumps({"error": "必须提供 url 或 command"}, ensure_ascii=False)

    store = _new_store()
    data = store.load_config()
    servers = data.setdefault("mcpServers", {})
    if name in servers:
        return json.dumps({"error": f"服务器 '{name}' 已存在"}, ensure_ascii=False)

    server_cfg: Dict[str, Any] = {"enabled": coerce_bool_arg(enabled, True)}
    if url:
        server_cfg["url"] = url
    if command:
        server_cfg["command"] = command
    if args:
        try:
            server_cfg["args"] = json.loads(args)
        except json.JSONDecodeError:
            server_cfg["args"] = args.split()
    if env:
        try:
            server_cfg["env"] = json.loads(env)
        except json.JSONDecodeError:
            return json.dumps({"error": "env 必须是合法 JSON 对象"}, ensure_ascii=False)
    if headers:
        try:
            server_cfg["headers"] = json.loads(headers)
        except json.JSONDecodeError:
            return json.dumps({"error": "headers 必须是合法 JSON 对象"}, ensure_ascii=False)
    if transport:
        server_cfg["transport"] = transport
    parsed_timeout = _coerce_positive_float_arg(timeout, "timeout")
    parsed_sse_timeout = _coerce_positive_float_arg(sse_read_timeout, "sse_read_timeout")
    parsed_call_timeout = _coerce_positive_float_arg(call_timeout, "call_timeout")
    if parsed_timeout is not None:
        server_cfg["timeout"] = parsed_timeout
    if parsed_sse_timeout is not None:
        server_cfg["sse_read_timeout"] = parsed_sse_timeout
    if parsed_call_timeout is not None:
        server_cfg["call_timeout"] = parsed_call_timeout

    result = await asyncio.to_thread(
        store.update_server_config,
        name,
        server_cfg,
        replace=True,
        create_if_missing=True,
        reload=True,
    )
    connected_map = bridge.get_connected_servers() if bridge else {}
    tools = connected_map.get(name, [])
    return _safe_json({
        "success": True,
        **result,
        "connected": name in connected_map,
        "tool_count": len(tools),
        "tools": tools[:_TOOL_RESULT_LIST_LIMIT],
    })


@mcp_tool_call(require_bridge=True)
async def _tool_remove_mcp_server(server_name: str, bridge: MCPBridge) -> str:
    """从配置文件删除 MCP 服务器并触发热重载。"""
    import asyncio

    store = _new_store()
    data = store.load_config()
    servers = data.get("mcpServers", {})
    if server_name not in servers:
        return json.dumps({"error": f"服务器 '{server_name}' 不存在"}, ensure_ascii=False)
    del servers[server_name]
    store.save_config(data)
    result = await asyncio.to_thread(bridge.reload_config)
    return json.dumps({"success": True, "server": server_name, "reload": result}, ensure_ascii=False)


@mcp_tool_call(require_bridge=True)
async def _tool_reload_mcp_config(bridge: MCPBridge) -> str:
    """手动触发 MCP 配置热重载。"""
    import asyncio
    result = await asyncio.to_thread(bridge.reload_config)
    return json.dumps({"success": True, "reload": result}, ensure_ascii=False)
