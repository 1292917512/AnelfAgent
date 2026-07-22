"""
MCP（Model Context Protocol）桥接模块。

功能：
1. 加载 MCP server 配置（JSON 格式）
2. 连接 MCP server，发现可用工具
3. 将 MCP server 注册为 MCP_SERVER 实体，工具注册为 TOOL 实体
4. 代理执行 MCP tool call
5. 配置热重载（无需重启即可增删改 server）

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
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.entity import EntityMetadata, EntityRegistry, EntityType, ToolParam
from core.sanitizer import is_sanitize_enabled, sanitize_text

_MAX_LIFECYCLE_RETRIES = 5
_DEFAULT_CALL_TIMEOUT = 300.0


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
    timeout: float = 5.0
    sse_read_timeout: float = 300.0
    call_timeout: float = _DEFAULT_CALL_TIMEOUT

    def fingerprint(self) -> Dict[str, Any]:
        """用于比较配置是否变更的字典（排除 name）。"""
        d = asdict(self)
        d.pop("name", None)
        return d


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
        from agent.config import get_config_provider
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


def _is_connection_closed(exc: Exception) -> bool:
    """判断异常是否为连接断开类型，递归处理 ExceptionGroup。"""
    if hasattr(exc, "exceptions"):
        return any(_is_connection_closed(sub) for sub in exc.exceptions)
    name = type(exc).__name__
    return name in ("ClosedResourceError", "BrokenResourceError") or "closed" in str(exc).lower()


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


def _safe_json(payload: Any) -> str:
    """序列化为 JSON 并脱敏，防止配置中的密钥进入 LLM 上下文与供应商日志。"""
    text = json.dumps(payload, ensure_ascii=False)
    return sanitize_text(text) if is_sanitize_enabled() else text


async def _list_roots_callback(context: Any) -> Any:
    """MCP roots 能力回调：向 server 声明允许写入的根目录。

    chrome-devtools-mcp 等 server 仅允许 filePath 写入 roots 之内
    （客户端未声明 roots 时默认只有 OS 临时目录）。将 workspace
    声明为 root 后，截图/快照等工具可直接保存到工作区。
    """
    from mcp import types

    from core.path import workspace_root

    ws = Path(workspace_root()).resolve()
    return types.ListRootsResult(
        roots=[types.Root(uri=ws.as_uri(), name="workspace")]
    )


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

        self.config = config or MCPConfig()
        self._sessions: Dict[str, Any] = {}           # name -> ClientSession
        self._stop_events: Dict[str, Any] = {}         # name -> asyncio.Event
        self._lifecycle_tasks: Dict[str, Any] = {}     # name -> asyncio.Future
        self._tool_server_map: Dict[str, str] = {}      # 注册名 -> server 名
        self._tool_original_names: Dict[str, str] = {}  # 注册名 -> MCP 原始工具名（仅冲突重命名时记录）
        self._last_errors: Dict[str, str] = {}          # name -> 最近一次连接错误详情
        self._op_locks: Dict[str, threading.Lock] = {}  # name -> 连接/断开操作串行锁
        self._lock = threading.Lock()

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
        """在 MCP 事件循环中执行协程并等待结果。

        等待超时或失败时取消底层协程，避免调用方已报失败但
        协程仍在后台继续执行造成的半连接状态。
        """
        import asyncio
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except BaseException:
            future.cancel()
            raise

    def _op_lock(self, name: str) -> threading.Lock:
        """返回指定 server 的连接/断开操作串行锁。"""
        with self._lock:
            lock = self._op_locks.get(name)
            if lock is None:
                lock = threading.Lock()
                self._op_locks[name] = lock
            return lock

    def _set_last_error(self, name: str, error: str) -> None:
        with self._lock:
            if error:
                self._last_errors[name] = error
            else:
                self._last_errors.pop(name, None)

    # ------------------------------------------------------------------
    # 公开同步接口
    # ------------------------------------------------------------------

    def connect_all(self) -> int:
        """连接所有已启用的 MCP server（同步，可从任意线程调用）。"""
        return self._run_coro(self._async_connect_all(), timeout=60)

    def connect_server_by_name(self, name: str) -> int:
        """按名称连接单个 server（同步，同一 server 的连接操作串行执行）。"""
        with self._op_lock(name):
            return self._run_coro(self._async_connect_server_by_name(name), timeout=30)

    def disconnect_server_by_name(self, name: str) -> None:
        """断开单个 server：向 lifecycle task 发送停止信号，注销工具。"""
        with self._op_lock(name):
            with self._lock:
                has_stop = name in self._stop_events
            if has_stop:
                self._run_coro(self._signal_stop(name), timeout=10)
            self._cleanup_server_entities(name)
            self._set_last_error(name, "")

    def reload_config(self) -> Dict[str, Any]:
        """热重载配置：重读磁盘配置，diff 增删改，自动连接/断开变更的 server。"""
        new_config = load_mcp_config()
        old_map = {s.name: s for s in self.config.servers}
        new_map = {s.name: s for s in new_config.servers}

        added: List[str] = []
        removed: List[str] = []
        changed: List[str] = []

        for name in new_map:
            if name not in old_map:
                added.append(name)
            elif new_map[name].fingerprint() != old_map[name].fingerprint():
                changed.append(name)

        for name in old_map:
            if name not in new_map:
                removed.append(name)

        for name in removed + changed:
            with self._lock:
                connected = name in self._sessions
            if connected:
                try:
                    self.disconnect_server_by_name(name)
                except Exception as exc:
                    log(f"热重载: 断开 '{name}' 失败: {exc}", "WARNING")

        self.config = new_config

        connected_names: List[str] = []
        for name in added + changed:
            srv = new_map[name]
            if srv.enabled:
                try:
                    self.connect_server_by_name(name)
                    connected_names.append(name)
                except Exception as exc:
                    detail = _extract_exception_detail(exc)
                    log(f"热重载: 连接 '{name}' 失败: {detail}", "WARNING")

        result = {
            "added": added, "removed": removed,
            "changed": changed, "connected": connected_names,
        }
        log(f"MCP 配置热重载完成: +{len(added)} -{len(removed)} ~{len(changed)}")
        return result

    def shutdown(self) -> None:
        """关闭所有连接，停止事件循环（进程退出时调用）。"""
        with self._lock:
            names = list(self._stop_events.keys())
        for name in names:
            try:
                self._run_coro(self._signal_stop(name), timeout=5)
            except Exception as e:
                log(f"MCP 服务停止失败: {e}", "DEBUG")
        self._loop.call_soon_threadsafe(self._loop.stop)

    def get_connected_servers(self) -> Dict[str, List[str]]:
        with self._lock:
            session_names = list(self._sessions.keys())
            tsm = dict(self._tool_server_map)
        result: Dict[str, List[str]] = {}
        for name in session_names:
            result[name] = [t for t, s in tsm.items() if s == name]
        return result

    def get_last_errors(self) -> Dict[str, str]:
        """返回各 server 最近一次连接错误（name → 错误详情）。"""
        with self._lock:
            return dict(self._last_errors)

    def list_available_servers(self) -> List[Dict[str, Any]]:
        """列出所有配置的 server 及其连接状态（供 AI 工具使用，url 已脱敏）。"""
        with self._lock:
            servers_snapshot = list(self.config.servers)
            session_names = set(self._sessions.keys())
            tsm = dict(self._tool_server_map)
            errors = dict(self._last_errors)
        mask = is_sanitize_enabled()
        servers: List[Dict[str, Any]] = []
        for srv in servers_snapshot:
            connected = srv.name in session_names
            tool_count = sum(1 for s in tsm.values() if s == srv.name)
            display_url = srv.url or srv.command
            servers.append({
                "name": srv.name,
                "url": sanitize_text(display_url) if mask else display_url,
                "enabled": srv.enabled,
                "connected": connected,
                "tool_count": tool_count,
                "last_error": errors.get(srv.name, ""),
            })
        return servers

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """代理执行 MCP tool call（调度到 MCP 事件循环执行）。"""
        with self._lock:
            server_name = self._tool_server_map.get(tool_name)
            has_session = server_name in self._sessions if server_name else False
            original_name = self._tool_original_names.get(tool_name, tool_name)
        if not server_name or not has_session:
            return json.dumps(
                {"error": f"MCP 工具未找到对应 server: {tool_name}"},
                ensure_ascii=False,
            )
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop is self._loop:
                return await self._do_call_tool(server_name, original_name, arguments)
            else:
                future = asyncio.run_coroutine_threadsafe(
                    self._do_call_tool(server_name, original_name, arguments),
                    self._loop,
                )
                return await asyncio.wrap_future(future)
        except Exception as exc:
            err_msg = str(exc) or f"{type(exc).__name__}: MCP 连接异常"
            log(f"MCP tool call 失败: {tool_name} → {err_msg}", "ERROR")
            return json.dumps({"error": err_msg}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 内部异步方法（在 MCP 事件循环中运行）
    # ------------------------------------------------------------------

    async def _signal_stop(self, name: str) -> None:
        """向指定 server 的 lifecycle task 发出停止信号并等待其退出。"""
        import asyncio
        with self._lock:
            stop_event = self._stop_events.get(name)
            task = self._lifecycle_tasks.get(name)
        if stop_event:
            stop_event.set()
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

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
        """连接单个 server，捕获异常防止影响其他 server 的并发连接。

        启动连接失败仅记录 last_error 供排查，不写回配置禁用——
        一次性网络抖动不应导致 server 被永久禁用。
        """
        try:
            count = await self._connect_server(srv)
            log(f"MCP server '{srv.name}' 已连接，发现 {count} 个工具")
            return count
        except Exception as exc:
            detail = _extract_exception_detail(exc)
            log(f"MCP server '{srv.name}' 连接失败: {detail}", "WARNING")
            self._set_last_error(srv.name, detail)
            return 0

    async def _async_connect_server_by_name(self, name: str) -> int:
        for srv in self.config.servers:
            if srv.name == name:
                return await self._connect_server(srv)
        raise ValueError(f"未找到 MCP server: {name}")

    async def _do_call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """执行 MCP 工具调用（带超时保护和断线重连）。"""
        import asyncio

        with self._lock:
            session = self._sessions.get(server_name)
        if not session:
            return json.dumps({"error": f"MCP server '{server_name}' 未连接"}, ensure_ascii=False)

        srv = self._find_server_config(server_name)
        timeout = srv.call_timeout if srv else _DEFAULT_CALL_TIMEOUT

        log(f"MCP call: {tool_name}({arguments})", "DEBUG", tag="mcp")
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments=arguments),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log(f"MCP tool call 超时 ({timeout}s): {tool_name}", "ERROR")
            return json.dumps(
                {"error": f"MCP 工具调用超时 ({timeout}s): {tool_name}"},
                ensure_ascii=False,
            )
        except Exception as first_exc:
            if not _is_connection_closed(first_exc):
                raise
            log(f"MCP server '{server_name}' 连接已断开，尝试重连...", "WARNING")
            if not await self._try_reconnect(server_name):
                return json.dumps(
                    {"error": f"MCP server '{server_name}' 连接已断开且重连失败"},
                    ensure_ascii=False,
                )
            with self._lock:
                session = self._sessions.get(server_name)
            if not session:
                return json.dumps(
                    {"error": f"MCP server '{server_name}' 重连后 session 不可用"},
                    ensure_ascii=False,
                )
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return json.dumps(
                    {"error": f"MCP 工具调用超时 ({timeout}s): {tool_name}"},
                    ensure_ascii=False,
                )

        if hasattr(result, "content"):
            parts = []
            for item in result.content:
                parts.append(item.text if hasattr(item, "text") else str(item))
            return "\n".join(parts) if parts else ""
        return str(result)

    def _find_server_config(self, name: str) -> Optional[MCPServerConfig]:
        """按名称查找 server 配置。"""
        for srv in self.config.servers:
            if srv.name == name:
                return srv
        return None

    async def _try_reconnect(self, server_name: str) -> bool:
        """尝试重连已断开的 MCP server，返回是否成功。"""
        import asyncio

        srv = self._find_server_config(server_name)
        if not srv:
            return False
        try:
            with self._lock:
                stop_event = self._stop_events.get(server_name)
                task = self._lifecycle_tasks.get(server_name)
            if stop_event:
                stop_event.set()
            if task:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

            with self._lock:
                self._sessions.pop(server_name, None)
                self._stop_events.pop(server_name, None)
                self._lifecycle_tasks.pop(server_name, None)

            self._cleanup_server_entities(server_name)

            count = await self._connect_server(srv)
            log(f"MCP server '{server_name}' 重连成功，发现 {count} 个工具")
            return True
        except Exception as exc:
            detail = _extract_exception_detail(exc)
            log(f"MCP server '{server_name}' 重连失败: {detail}", "ERROR")
            return False

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _cleanup_server_entities(self, name: str) -> None:
        """从 EntityRegistry 和工具映射中移除指定 server 的所有工具。"""
        with self._lock:
            tools = [t for t, s in self._tool_server_map.items() if s == name]
            for t in tools:
                self._tool_server_map.pop(t, None)
                self._tool_original_names.pop(t, None)
        for t in tools:
            try:
                EntityRegistry.unregister(t)
            except (KeyError, ValueError):
                pass
        try:
            EntityRegistry.unregister(f"mcp:{name}")
        except (KeyError, ValueError):
            pass

    async def _connect_server(self, srv: MCPServerConfig) -> int:
        """连接单个 MCP server：启动 lifecycle task，等待 session 就绪，返回工具数量。

        lifecycle task 持有 transport context manager 的完整生命周期，
        保证 enter/exit 在同一 asyncio task 内，避免 anyio cancel scope 跨 task 错误。
        同名 lifecycle task 仍存活时先将其停止，避免重复连接导致旧 task 失联泄漏。
        """
        import asyncio

        with self._lock:
            old_task = self._lifecycle_tasks.get(srv.name)
        if old_task is not None and not old_task.done():
            log(f"MCP server '{srv.name}' 已存在连接任务，先停止旧任务再重连", "DEBUG")
            await self._signal_stop(srv.name)

        ready_event: asyncio.Event = asyncio.Event()
        stop_event: asyncio.Event = asyncio.Event()
        result_box: List[Any] = []  # [tool_count] 或 [Exception]

        task = asyncio.ensure_future(
            self._server_lifecycle(srv, stop_event, ready_event, result_box)
        )
        with self._lock:
            self._stop_events[srv.name] = stop_event
            self._lifecycle_tasks[srv.name] = task

        # 等待 session 初始化完成（或失败）
        await ready_event.wait()

        if result_box and isinstance(result_box[0], Exception):
            with self._lock:
                self._stop_events.pop(srv.name, None)
                self._lifecycle_tasks.pop(srv.name, None)
            self._set_last_error(srv.name, _extract_exception_detail(result_box[0]))
            raise result_box[0]

        self._set_last_error(srv.name, "")
        return result_box[0] if result_box else 0

    async def _server_lifecycle(
        self,
        srv: MCPServerConfig,
        stop_event: Any,
        ready_event: Any,
        result_box: List[Any],
    ) -> None:
        """每个 server 的持久连接任务（带自动重连）。

        transport context manager 的 __aenter__ / __aexit__ 全部在此 task 内执行，
        彻底避免 anyio cancel scope 跨 task 的 RuntimeError。
        首次连接失败直接报错；后续连接断开自动重试（指数退避）。
        """
        import asyncio
        from mcp import ClientSession

        first_attempt = True

        try:
            for attempt in range(_MAX_LIFECYCLE_RETRIES):
                if stop_event.is_set():
                    break
                try:
                    transport_cm = self._create_transport(srv)
                    async with transport_cm as streams:
                        read_stream, write_stream = streams[0], streams[1]
                        async with ClientSession(
                            read_stream,
                            write_stream,
                            list_roots_callback=_list_roots_callback,
                        ) as session:
                            await session.initialize()
                            with self._lock:
                                self._sessions[srv.name] = session

                            if first_attempt:
                                count = await self._register_server_tools(srv, session)
                                result_box.append(count)
                                ready_event.set()
                                first_attempt = False
                            else:
                                self._cleanup_server_entities(srv.name)
                                count = await self._register_server_tools(srv, session)
                                self._set_last_error(srv.name, "")
                                log(f"MCP server '{srv.name}' 自动重连成功 (第 {attempt + 1} 次)，{count} 个工具")

                            await stop_event.wait()
                            return

                except Exception as exc:
                    detail = _extract_exception_detail(exc)
                    if first_attempt:
                        log(f"MCP server '{srv.name}' lifecycle 异常: {detail}", "ERROR")
                        result_box.append(exc)
                        ready_event.set()
                        return

                    if stop_event.is_set():
                        return

                    with self._lock:
                        self._sessions.pop(srv.name, None)
                    self._set_last_error(srv.name, f"连接断开: {detail}")

                    wait = min(2 ** attempt, 60)
                    log(
                        f"MCP server '{srv.name}' 连接断开: {detail}，"
                        f"{wait}s 后重试 ({attempt + 1}/{_MAX_LIFECYCLE_RETRIES})",
                        "WARNING",
                    )
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=wait)
                        return
                    except asyncio.TimeoutError:
                        pass

            if not first_attempt:
                log(f"MCP server '{srv.name}' 重试 {_MAX_LIFECYCLE_RETRIES} 次后放弃", "ERROR")
        finally:
            with self._lock:
                self._sessions.pop(srv.name, None)
                self._stop_events.pop(srv.name, None)
                self._lifecycle_tasks.pop(srv.name, None)
            if not first_attempt:
                self._cleanup_server_entities(srv.name)

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
        mcp_tools = list(tools_result.tools)
        tool_names = self._register_tool_entries(srv.name, mcp_tools)

        # 分组目录描述：让 AI 在工具分组目录中识别该服务的用途
        EntityRegistry.register_group(
            f"mcp:{srv.name}",
            self._build_group_description(srv.name, mcp_tools),
        )

        mcp_entity = EntityRegistry.get(f"mcp:{srv.name}")
        if mcp_entity:
            mcp_entity.meta["tools"] = tool_names

        return len(tool_names)

    @staticmethod
    def _build_group_description(server_name: str, tools: List[Any]) -> str:
        """生成 mcp:<server> 分组目录描述（工具名 + 一句话用途）。"""
        briefs: List[str] = []
        for t in tools[:8]:
            t_name = getattr(t, "name", "") or ""
            t_desc = (getattr(t, "description", "") or "").strip().split("\n")[0][:40]
            briefs.append(f"{t_name}({t_desc})" if t_desc else t_name)
        suffix = "…" if len(tools) > 8 else ""
        desc = f"MCP 服务 {server_name}，工具: {', '.join(briefs)}{suffix}"
        return desc[:300]

    def _register_tool_entries(self, server_name: str, tools: List[Any]) -> List[str]:
        """将 server 的工具批量注册到 EntityRegistry，返回注册名列表。

        工具名与现有实体（内置工具或其他 MCP 工具）冲突时，
        自动加 ``{server}__`` 前缀注册，避免覆盖同名实体。
        """
        registered: List[str] = []
        for t in tools:
            t_name, t_params = self._parse_mcp_tool(t)
            bridge = self

            reg_name = t_name
            if EntityRegistry.exists(reg_name):
                reg_name = f"{server_name}__{t_name}"
                log(
                    f"MCP 工具名冲突: '{t_name}' 已被占用，"
                    f"server '{server_name}' 的工具注册为 '{reg_name}'",
                    "WARNING",
                )

            async def _proxy(_name: str = reg_name, **kwargs: Any) -> str:
                return await bridge.call_tool(_name, kwargs)

            EntityRegistry.register_tool(
                name=reg_name,
                func=_proxy,
                description=getattr(t, "description", "") or t_name,
                group=f"mcp:{server_name}",
                params=t_params,
                tags=["mcp", server_name],
                source="mcp",
            )
            with self._lock:
                self._tool_server_map[reg_name] = server_name
                if reg_name != t_name:
                    self._tool_original_names[reg_name] = t_name
            registered.append(reg_name)
        return registered

    @staticmethod
    def _create_transport(srv: MCPServerConfig) -> Any:
        """根据配置创建传输上下文管理器。"""
        transport = srv.transport or ("stdio" if srv.command else "streamable_http")

        if transport == "stdio":
            from mcp.client.stdio import stdio_client, StdioServerParameters
            stdio_env = {
                **os.environ,
                "ANELF_MCP_STDIO": "1",
                "ANELF_LOG_STREAM": "stderr",
                "PYTHONUNBUFFERED": "1",
            }
            if srv.env:
                stdio_env.update(srv.env)
            return stdio_client(StdioServerParameters(
                command=srv.command,
                args=srv.args,
                env=stdio_env,
            ))

        if transport == "streamable_http":
            from mcp.client.streamable_http import streamablehttp_client
            return streamablehttp_client(
                url=srv.url,
                headers=srv.headers or None,
                timeout=srv.timeout,
            )

        if transport == "sse":
            from mcp.client.sse import sse_client
            return sse_client(
                srv.url,
                headers=srv.headers or None,
                timeout=srv.timeout,
                sse_read_timeout=srv.sse_read_timeout,
            )

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


def _tool_list_mcp_servers() -> str:
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    servers = bridge.list_available_servers()
    return json.dumps({"servers": servers, "total": len(servers)}, ensure_ascii=False)


def _tool_get_mcp_server_config(server_name: str = "") -> str:
    """查看 MCP 原始配置（单个或全部，输出已脱敏）。"""
    try:
        from services import MCPService

        svc = MCPService()
        schema = svc.get_server_config_schema()
        if server_name.strip():
            cfg = svc.get_server_config(server_name.strip())
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

        full = svc.load_config()
        return _safe_json({
            "mcpServers": full.get("mcpServers", {}),
            "editable_schema": schema,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


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
            pass
    return [x.strip() for x in raw.split(",") if x.strip()]


def _coerce_bool_arg(value: Any, default: bool) -> bool:
    """将工具参数稳健转为 bool（兼容 LLM 误传字符串）。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default
    return bool(value)


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
        return json.dumps({"error": f"patch_json 不是合法 JSON: {exc}"}, ensure_ascii=False)
    if not isinstance(patch, dict):
        return json.dumps({"error": "patch_json 必须是 JSON 对象字符串"}, ensure_ascii=False)

    try:
        from services import MCPService

        svc = MCPService()
        result = await asyncio.to_thread(
            svc.update_server_config,
            server_name.strip(),
            patch,
            replace=_coerce_bool_arg(replace, False),
            remove_fields=_parse_remove_fields_arg(remove_fields),
            create_if_missing=_coerce_bool_arg(create_if_missing, False),
            reload=_coerce_bool_arg(reload, True),
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
            "tools": tools[:50],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def _tool_set_mcp_server_enabled(
    server_name: str,
    enabled: bool,
    reload: bool = True,
) -> str:
    """显式设置 enabled 状态。"""
    import asyncio

    try:
        from services import MCPService

        svc = MCPService()
        result = await asyncio.to_thread(
            svc.set_server_enabled,
            server_name.strip(),
            _coerce_bool_arg(enabled, False),
            reload=_coerce_bool_arg(reload, True),
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
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _tool_get_mcp_config_template() -> str:
    """返回 MCP 配置模板与 patch 示例。"""
    try:
        from services import MCPService

        schema = MCPService.get_server_config_schema()
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
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def _tool_connect_mcp_server(server_name: str) -> str:
    """异步连接 MCP 服务器，不阻塞 Mind 思考循环。"""
    import asyncio
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    try:
        count = await asyncio.to_thread(bridge.connect_server_by_name, server_name)
        try:
            from services import MCPService
            svc = MCPService()
            await asyncio.to_thread(svc.set_server_enabled, server_name, True, reload=False)
        except Exception as inner_exc:
            log(f"同步 enabled 状态失败(connect): {inner_exc}", "DEBUG", tag="mcp")
        return json.dumps({
            "success": True,
            "server": server_name,
            "tools_discovered": count,
            "enabled": True,
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
        try:
            from services import MCPService
            svc = MCPService()
            await asyncio.to_thread(svc.set_server_enabled, server_name, False, reload=False)
        except Exception as inner_exc:
            log(f"同步 enabled 状态失败(disconnect): {inner_exc}", "DEBUG", tag="mcp")
        return json.dumps(
            {"success": True, "server": server_name, "action": "disconnected", "enabled": False},
            ensure_ascii=False,
        )
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
            try:
                from services import MCPService
                svc = MCPService()
                await asyncio.to_thread(svc.set_server_enabled, server_name, False, reload=False)
            except Exception as inner_exc:
                log(f"同步 enabled 状态失败(toggle->disconnect): {inner_exc}", "DEBUG", tag="mcp")
            return json.dumps(
                {"success": True, "server": server_name, "action": "disconnected", "enabled": False},
                ensure_ascii=False,
            )
        else:
            count = await asyncio.to_thread(bridge.connect_server_by_name, server_name)
            try:
                from services import MCPService
                svc = MCPService()
                await asyncio.to_thread(svc.set_server_enabled, server_name, True, reload=False)
            except Exception as inner_exc:
                log(f"同步 enabled 状态失败(toggle->connect): {inner_exc}", "DEBUG", tag="mcp")
            return json.dumps({
                "success": True,
                "server": server_name,
                "action": "connected",
                "tools_discovered": count,
                "enabled": True,
            }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


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
) -> str:
    """添加 MCP 服务器到配置文件并触发热重载。"""
    import asyncio
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    if not url and not command:
        return json.dumps({"error": "必须提供 url 或 command"}, ensure_ascii=False)
    try:
        from services import MCPService
        svc = MCPService()
        data = svc.load_config()
        servers = data.setdefault("mcpServers", {})
        if name in servers:
            return json.dumps({"error": f"服务器 '{name}' 已存在"}, ensure_ascii=False)

        server_cfg: Dict[str, Any] = {"enabled": _coerce_bool_arg(enabled, True)}
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
            svc.update_server_config,
            name,
            server_cfg,
            replace=True,
            create_if_missing=True,
            reload=True,
        )
        connected_map = bridge.get_connected_servers()
        tools = connected_map.get(name, [])
        return _safe_json({
            "success": True,
            **result,
            "connected": name in connected_map,
            "tool_count": len(tools),
            "tools": tools[:50],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def _tool_remove_mcp_server(server_name: str) -> str:
    """从配置文件删除 MCP 服务器并触发热重载。"""
    import asyncio
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    try:
        from services import MCPService
        svc = MCPService()
        data = svc.load_config()
        servers = data.get("mcpServers", {})
        if server_name not in servers:
            return json.dumps({"error": f"服务器 '{server_name}' 不存在"}, ensure_ascii=False)
        del servers[server_name]
        svc.save_config(data)
        result = await asyncio.to_thread(bridge.reload_config)
        return json.dumps({"success": True, "server": server_name, "reload": result}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def _tool_reload_mcp_config() -> str:
    """手动触发 MCP 配置热重载。"""
    import asyncio
    bridge = get_mcp_bridge()
    if not bridge:
        return json.dumps({"error": "MCP Bridge 未初始化"}, ensure_ascii=False)
    try:
        result = await asyncio.to_thread(bridge.reload_config)
        return json.dumps({"success": True, "reload": result}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
