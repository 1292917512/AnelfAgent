"""
MCP（Model Context Protocol）桥接模块。

功能：
1. 加载 MCP server 配置（JSON 格式）
2. 连接 MCP server，发现可用工具
3. 将 MCP server 注册为 MCP_SERVER 实体，工具注册为 TOOL 实体
4. 代理执行 MCP tool call

配置格式（mcp_servers.json）::

    {
      "servers": [
        {
          "name": "filesystem",
          "command": "npx",
          "args": ["-y", "@anthropic/mcp-filesystem"],
          "env": { "ALLOWED_DIR": "/tmp" },
          "enabled": true
        },
        {
          "name": "remote-api",
          "url": "http://localhost:8080/mcp",
          "enabled": true
        }
      ]
    }

依赖：mcp（pip install mcp）
若未安装 mcp SDK，本模块不会导致 import 崩溃，只会在实际调用时报错。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.entity import EntityMetadata, EntityRegistry, EntityType, ToolParam


# ------------------------------------------------------------------
# Config models
# ------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """单个 MCP server 配置。

    支持三种传输方式：
    - stdio: 填 command + args（启动子进程）
    - sse: 填 url（SSE 传输，旧协议）
    - streamable_http: 填 url（Streamable HTTP 传输，新协议，默认）
    """

    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    transport: str = ""
    enabled: bool = False


@dataclass
class MCPConfig:
    """MCP 全局配置。"""

    servers: List[MCPServerConfig] = field(default_factory=list)


def _resolve_config_path() -> Optional[str]:
    """定位 MCP 配置文件路径。"""
    env = os.getenv("ANELF_MCP_CONFIG", "")
    if env:
        return env
    try:
        from agent.ext.config_provider import get_config_provider
        p = Path(get_config_provider()._config.mcp_config_path)
        if p.exists():
            return str(p)
    except Exception as e:
        log(f"MCP 配置路径获取失败: {e}", "DEBUG")
    for c in [Path("config/mcp_servers.json"), Path("mcp_servers.json")]:
        if c.exists():
            return str(c)
    return None


def _parse_mcp_data(data: Dict[str, Any]) -> List[MCPServerConfig]:
    """从 JSON dict 解析 server 列表（兼容 Cursor mcpServers 格式和旧格式）。"""
    servers: List[MCPServerConfig] = []
    if "mcpServers" in data:
        for name, cfg in data["mcpServers"].items():
            if not isinstance(cfg, dict):
                continue
            fields = {k: v for k, v in cfg.items() if k in MCPServerConfig.__dataclass_fields__}
            fields["name"] = name
            servers.append(MCPServerConfig(**fields))
    elif "servers" in data:
        for s in data["servers"]:
            servers.append(MCPServerConfig(
                **{k: v for k, v in s.items() if k in MCPServerConfig.__dataclass_fields__}
            ))
    return servers


def load_mcp_config(path: Optional[str] = None) -> MCPConfig:
    """从 JSON 文件加载 MCP 配置。"""
    path = path or _resolve_config_path()
    if not path or not Path(path).exists():
        return MCPConfig()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return MCPConfig(servers=_parse_mcp_data(data))
    except Exception as exc:
        log(f"加载 MCP 配置失败: {exc}", "ERROR")
        return MCPConfig()


def _extract_exception_detail(exc: Exception) -> str:
    """从 ExceptionGroup 中递归提取真实的子异常信息。

    anyio 的 TaskGroup 在子任务失败时抛出 ExceptionGroup，
    其 str() 只显示 "unhandled errors in a TaskGroup (N sub-exception)"，
    真正的原因（如 ConnectionRefused）藏在 .exceptions 中。
    """
    if hasattr(exc, "exceptions"):
        causes = [_extract_exception_detail(sub) for sub in exc.exceptions]
        return "; ".join(causes)
    return f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------
# MCP Bridge
# ------------------------------------------------------------------


class MCPBridge:
    """MCP 工具桥接器（独立事件循环 + 每 server 独立 lifecycle task）。

    每个 MCP server 的连接在专用 lifecycle task 中运行，保证
    transport context manager 的 enter/exit 始终在同一个 asyncio task 内，
    避免 anyio cancel scope 跨 task 的 RuntimeError。
    """

    def __init__(self, config: Optional[MCPConfig] = None) -> None:
        import asyncio
        import threading

        self.config = config or MCPConfig()
        self._sessions: Dict[str, Any] = {}           # name -> ClientSession
        self._stop_events: Dict[str, Any] = {}         # name -> asyncio.Event
        self._lifecycle_tasks: Dict[str, Any] = {}     # name -> asyncio.Future
        self._tool_server_map: Dict[str, str] = {}

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="mcp-bridge",
        )
        self._thread.start()

    def _run_loop(self) -> None:
        import asyncio
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coro(self, coro: Any, timeout: float = 60) -> Any:
        """在 MCP 事件循环中执行协程并等待结果。"""
        import asyncio
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def connect_all(self) -> int:
        """连接所有已启用的 MCP server（同步，可从任意线程调用）。"""
        return self._run_coro(self._async_connect_all(), timeout=60)

    def connect_server_by_name(self, name: str) -> int:
        """按名称连接单个 server（同步）。"""
        return self._run_coro(self._async_connect_server_by_name(name), timeout=30)

    def disconnect_server_by_name(self, name: str) -> None:
        """断开单个 server：向 lifecycle task 发送停止信号，注销工具。"""
        if name in self._stop_events:
            # 通知 lifecycle task 退出——transport 的 __aexit__ 在该 task 内自然执行
            self._run_coro(self._signal_stop(name), timeout=10)

        tools_to_remove = [t for t, s in self._tool_server_map.items() if s == name]
        for t in tools_to_remove:
            del self._tool_server_map[t]
            try:
                EntityRegistry.unregister(t)
            except (KeyError, ValueError):
                pass
        try:
            EntityRegistry.unregister(f"mcp:{name}")
        except (KeyError, ValueError):
            pass
        log(f"MCP server '{name}' 已断开，移除 {len(tools_to_remove)} 个工具")

    async def _signal_stop(self, name: str) -> None:
        """向指定 server 的 lifecycle task 发出停止信号并等待其退出。"""
        import asyncio
        stop_event = self._stop_events.get(name)
        if stop_event:
            stop_event.set()
        task = self._lifecycle_tasks.get(name)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                task.cancel()

    def shutdown(self) -> None:
        """关闭所有连接，停止事件循环（进程退出时调用）。"""
        names = list(self._stop_events.keys())
        for name in names:
            try:
                self._run_coro(self._signal_stop(name), timeout=5)
            except Exception as e:
                log(f"MCP 服务停止失败: {e}", "DEBUG")
        self._loop.call_soon_threadsafe(self._loop.stop)

    def get_connected_servers(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for name in self._sessions:
            tools = [t for t, s in self._tool_server_map.items() if s == name]
            result[name] = tools
        return result

    def list_available_servers(self) -> List[Dict[str, Any]]:
        """列出所有配置的 server 及其连接状态（供 AI 工具使用）。"""
        servers: List[Dict[str, Any]] = []
        for srv in self.config.servers:
            connected = srv.name in self._sessions
            tool_count = len([t for t, s in self._tool_server_map.items() if s == srv.name])
            servers.append({
                "name": srv.name,
                "url": srv.url or srv.command,
                "connected": connected,
                "tool_count": tool_count,
            })
        return servers

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """代理执行 MCP tool call（调度到 MCP 事件循环执行）。"""
        server_name = self._tool_server_map.get(tool_name)
        if not server_name or server_name not in self._sessions:
            return json.dumps(
                {"error": f"MCP 工具未找到对应 server: {tool_name}"},
                ensure_ascii=False,
            )
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop is self._loop:
                return await self._do_call_tool(server_name, tool_name, arguments)
            else:
                future = asyncio.run_coroutine_threadsafe(
                    self._do_call_tool(server_name, tool_name, arguments),
                    self._loop,
                )
                return await asyncio.wrap_future(future)
        except Exception as exc:
            log(f"MCP tool call 失败: {tool_name} → {exc}", "ERROR")
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 内部异步方法（在 MCP 事件循环中运行）
    # ------------------------------------------------------------------

    async def _async_disconnect_server(self, name: str) -> None:
        """已由 _signal_stop 取代，保留空实现以兼容可能的外部调用。"""
        await self._signal_stop(name)

    async def _async_connect_all(self) -> int:
        """并发连接所有启用的 MCP server。"""
        import asyncio
        coros = [
            self._connect_one_safe(srv)
            for srv in self.config.servers
            if srv.enabled
        ]
        if not coros:
            return 0
        results = await asyncio.gather(*coros, return_exceptions=True)
        return sum(r for r in results if isinstance(r, int))

    async def _connect_one_safe(self, srv: MCPServerConfig) -> int:
        """连接单个 server，捕获异常防止影响其他 server 的并发连接。"""
        try:
            count = await self._connect_server(srv)
            log(f"MCP server '{srv.name}' 已连接，发现 {count} 个工具")
            return count
        except Exception as exc:
            detail = _extract_exception_detail(exc)
            log(f"MCP server '{srv.name}' 连接失败: {detail}", "ERROR")
            return 0

    async def _async_connect_server_by_name(self, name: str) -> int:
        for srv in self.config.servers:
            if srv.name == name:
                return await self._connect_server(srv)
        raise ValueError(f"未找到 MCP server: {name}")

    async def _do_call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        import asyncio
        session = self._sessions[server_name]
        log(f"MCP call: {tool_name}({arguments})", "DEBUG", tag="mcp")
        result = await session.call_tool(tool_name, arguments=arguments)
        if hasattr(result, "content"):
            parts = []
            for item in result.content:
                parts.append(item.text if hasattr(item, "text") else str(item))
            return "\n".join(parts) if parts else ""
        return str(result)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _connect_server(self, srv: MCPServerConfig) -> int:
        """连接单个 MCP server：启动 lifecycle task，等待 session 就绪，返回工具数量。

        lifecycle task 持有 transport context manager 的完整生命周期，
        保证 enter/exit 在同一 asyncio task 内，避免 anyio cancel scope 跨 task 错误。
        """
        import asyncio

        ready_event: asyncio.Event = asyncio.Event()
        stop_event: asyncio.Event = asyncio.Event()
        result_box: List[Any] = []  # [tool_count] 或 [Exception]

        task = asyncio.ensure_future(
            self._server_lifecycle(srv, stop_event, ready_event, result_box)
        )
        self._stop_events[srv.name] = stop_event
        self._lifecycle_tasks[srv.name] = task

        # 等待 session 初始化完成（或失败）
        await ready_event.wait()

        if result_box and isinstance(result_box[0], Exception):
            self._stop_events.pop(srv.name, None)
            self._lifecycle_tasks.pop(srv.name, None)
            raise result_box[0]

        return result_box[0] if result_box else 0

    async def _server_lifecycle(
        self,
        srv: MCPServerConfig,
        stop_event: Any,
        ready_event: Any,
        result_box: List[Any],
    ) -> None:
        """每个 server 的持久连接任务。

        transport context manager 的 __aenter__ / __aexit__ 全部在此 task 内执行，
        彻底避免 anyio cancel scope 跨 task 的 RuntimeError。
        """
        import asyncio
        from mcp import ClientSession

        try:
            transport_cm = self._create_transport(srv)
            async with transport_cm as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._sessions[srv.name] = session

                    # 注册 server 实体和工具
                    count = await self._register_server_tools(srv, session)
                    result_box.append(count)
                    ready_event.set()

                    # 保持连接，直到收到停止信号
                    await stop_event.wait()

        except Exception as exc:
            detail = _extract_exception_detail(exc)
            log(f"MCP server '{srv.name}' lifecycle 异常: {detail}", "ERROR")
            if not ready_event.is_set():
                result_box.append(exc)
                ready_event.set()
        finally:
            self._sessions.pop(srv.name, None)
            self._stop_events.pop(srv.name, None)
            self._lifecycle_tasks.pop(srv.name, None)

    async def _register_server_tools(self, srv: MCPServerConfig, session: Any) -> int:
        """注册 MCP server 实体和其工具到 EntityRegistry。"""
        transport_type = srv.transport or ("stdio" if srv.command else "streamable_http")
        EntityRegistry.register(EntityMetadata(
            name=f"mcp:{srv.name}",
            entity_type=EntityType.MCP_SERVER,
            description=f"MCP server: {srv.name}",
            enabled=True,
            group="mcp",
            source="mcp",
            tags=["mcp", srv.name],
            instance=self,
            meta={
                "transport": transport_type,
                "command": srv.command,
                "url": srv.url,
                "connected": True,
            },
        ))

        tools_result = await session.list_tools()
        tool_names: List[str] = []
        count = 0
        for t in tools_result.tools:
            t_name, t_params = self._parse_mcp_tool(t)
            bridge = self

            async def _proxy(_name: str = t_name, **kwargs: Any) -> str:
                return await bridge.call_tool(_name, kwargs)

            EntityRegistry.register_tool(
                name=t_name,
                func=_proxy,
                description=getattr(t, "description", "") or t_name,
                group=f"mcp:{srv.name}",
                params=t_params,
                tags=["mcp", srv.name],
                source="mcp",
            )
            self._tool_server_map[t_name] = srv.name
            tool_names.append(t_name)
            count += 1

        mcp_entity = EntityRegistry.get(f"mcp:{srv.name}")
        if mcp_entity:
            mcp_entity.meta["tools"] = tool_names

        return count

    @staticmethod
    def _create_transport(srv: MCPServerConfig) -> Any:
        """根据配置创建传输上下文管理器。"""
        transport = srv.transport or ("stdio" if srv.command else "streamable_http")

        if transport == "stdio":
            from mcp.client.stdio import stdio_client, StdioServerParameters
            return stdio_client(StdioServerParameters(
                command=srv.command,
                args=srv.args,
                env={**os.environ, **srv.env} if srv.env else None,
            ))

        if transport == "streamable_http":
            from mcp.client.streamable_http import streamablehttp_client
            return streamablehttp_client(url=srv.url, headers=srv.headers or None)

        if transport == "sse":
            from mcp.client.sse import sse_client
            return sse_client(srv.url, headers=srv.headers or None)

        raise ValueError(f"不支持的传输类型: {transport}")

    @staticmethod
    def _parse_mcp_tool(mcp_tool: Any) -> tuple[str, List[ToolParam]]:
        """解析 MCP Tool 对象为名称和参数列表。"""
        name = mcp_tool.name
        params: List[ToolParam] = []
        input_schema = getattr(mcp_tool, "inputSchema", None) or {}
        if isinstance(input_schema, dict):
            properties = input_schema.get("properties", {})
            required_list = input_schema.get("required", [])
            for p_name, p_schema in properties.items():
                params.append(ToolParam(
                    name=p_name,
                    description=p_schema.get("description", ""),
                    type=p_schema.get("type", "string"),
                    required=p_name in required_list,
                    enum=p_schema.get("enum"),
                ))
        return name, params


# 全局单例
_mcp_bridge: Optional[MCPBridge] = None


def get_mcp_bridge() -> Optional[MCPBridge]:
    return _mcp_bridge


def set_mcp_bridge(bridge: MCPBridge) -> None:
    global _mcp_bridge
    _mcp_bridge = bridge


# ------------------------------------------------------------------
# AI 工具：让 AI 能自主管理 MCP server
# ------------------------------------------------------------------


def register_mcp_tools() -> None:
    """注册 MCP 管理工具到 EntityRegistry。"""
    bridge = get_mcp_bridge()
    server_names = [s.name for s in bridge.config.servers] if bridge else []
    names_hint = f" ({', '.join(server_names)})" if server_names else ""
    EntityRegistry.register_group("mcp_manage", f"MCP 管理 - 查看/连接/断开 MCP 服务器{names_hint}")

    EntityRegistry.register_tool(
        name="list_mcp_servers",
        func=_tool_list_mcp_servers,
        description="列出所有可用的 MCP 服务器及其连接状态和工具数量。",
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

    log("MCP 管理工具已注册 (list / connect / disconnect / toggle)", tag="思维")


def _tool_list_mcp_servers() -> str:
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    servers = bridge.list_available_servers()
    return json.dumps({"servers": servers, "total": len(servers)}, ensure_ascii=False)


async def _tool_connect_mcp_server(server_name: str) -> str:
    """异步连接 MCP 服务器，不阻塞 Mind 思考循环。"""
    import asyncio
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    try:
        count = await asyncio.to_thread(bridge.connect_server_by_name, server_name)
        return json.dumps({
            "success": True,
            "server": server_name,
            "tools_discovered": count,
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def _tool_disconnect_mcp_server(server_name: str) -> str:
    """异步断开 MCP 服务器。"""
    import asyncio
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    if server_name not in bridge.get_connected_servers():
        return json.dumps({"error": f"服务器 '{server_name}' 未连接"}, ensure_ascii=False)
    try:
        await asyncio.to_thread(bridge.disconnect_server_by_name, server_name)
        return json.dumps({"success": True, "server": server_name, "action": "disconnected"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def _tool_toggle_mcp_server(server_name: str) -> str:
    """自动判断当前状态并切换 MCP 服务器的连接。"""
    import asyncio
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    try:
        if server_name in bridge.get_connected_servers():
            await asyncio.to_thread(bridge.disconnect_server_by_name, server_name)
            return json.dumps({"success": True, "server": server_name, "action": "disconnected"}, ensure_ascii=False)
        else:
            count = await asyncio.to_thread(bridge.connect_server_by_name, server_name)
            return json.dumps({
                "success": True,
                "server": server_name,
                "action": "connected",
                "tools_discovered": count,
            }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
