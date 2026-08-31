"""MCP（Model Context Protocol）桥接核心：连接生命周期与调用代理。

职责：
1. 连接 MCP server，发现可用工具（配置模型与加载见 entities.mcp.config）；
2. 将 MCP server 注册为 MCP_SERVER 实体，工具注册为 TOOL 实体；
3. 代理执行 MCP tool call（超时保护 + 断线重连 + 结果渲染）；
4. 配置热重载（无需重启即可增删改 server）。

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

import inspect
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.entity import EntityMetadata, EntityRegistry, EntityType
from core.log import log
from core.sanitizer import is_sanitize_enabled, sanitize_text
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities.mcp.config import (
    _DEFAULT_CALL_TIMEOUT,
    MCPConfig,
    MCPServerConfig,
    _mcp_sleep_enabled,
    load_mcp_config,
)
from entities.mcp.render import _extract_text_blocks, _render_call_result
from entities.mcp.retry import _RetryBudget
from entities.mcp.schema import _parse_mcp_tool, _sanitize_tool_name
from entities.mcp.transport import _create_transport, _list_roots_callback

_MAX_LIFECYCLE_RETRIES = 5
# 工具列表变更通知的同步防抖（秒）：server 可能连发多条通知，合并为一次同步
_TOOL_SYNC_DEBOUNCE_SEC = 1.0
# 连接稳定运行该时长后，重连重试预算复位（秒）：
# 长期运行的服务偶发抖动不应累计耗尽预算而永久死亡（对齐 dsh 稳定窗口复位）
_RECONNECT_BUDGET_RESET_SEC = 300.0

# 分组目录描述的截断上限
_GROUP_DESC_TOOL_LIMIT = 8        # 描述中列出的工具数量上限
_GROUP_DESC_TOOL_BRIEF_LEN = 40   # 单个工具一句话描述截断长度
_GROUP_DESC_MAX_LEN = 300         # 描述整体长度上限


def _is_connection_closed(exc: Exception) -> bool:
    """判断异常是否为连接断开类型，递归处理 ExceptionGroup。"""
    if hasattr(exc, "exceptions"):
        return any(_is_connection_closed(sub) for sub in exc.exceptions)
    name = type(exc).__name__
    return name in ("ClosedResourceError", "BrokenResourceError") or "closed" in str(exc).lower()


def extract_exception_detail(exc: Exception) -> str:
    """从 ExceptionGroup 中递归提取真实的子异常信息。

    anyio 的 TaskGroup 在子任务失败时抛出 ExceptionGroup，
    其 str() 只显示 "unhandled errors in a TaskGroup (N sub-exception)"，
    真正的原因（如 ConnectionRefused）藏在 .exceptions 中。
    """
    if hasattr(exc, "exceptions"):
        causes = [extract_exception_detail(sub) for sub in exc.exceptions]
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

        self.config = config or MCPConfig()
        self._sessions: Dict[str, Any] = {}           # name -> ClientSession
        self._stop_events: Dict[str, Any] = {}         # name -> asyncio.Event
        self._lifecycle_tasks: Dict[str, Any] = {}     # name -> asyncio.Future
        self._tool_server_map: Dict[str, str] = {}      # 注册名 -> server 名
        self._tool_original_names: Dict[str, str] = {}  # 注册名 -> MCP 原始工具名（仅冲突重命名时记录）
        self._last_errors: Dict[str, str] = {}          # name -> 最近一次连接错误详情
        self._op_locks: Dict[str, threading.Lock] = {}  # name -> 连接/断开操作串行锁
        self._sync_pending: set = set()                 # name -> 工具列表同步防抖中
        self._lock = threading.Lock()
        self._reload_lock = threading.Lock()  # reload_config 全局串行

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
        """热重载配置：重读磁盘配置，diff 增删改，自动连接/断开变更的 server。

        全局串行：并发 reload（如 web 侧快速连写）会基于被中途替换的
        self.config 算出错误的增删集合。
        """
        with self._reload_lock:
            return self._reload_config_locked()

    def _reload_config_locked(self) -> Dict[str, Any]:
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
                # 重连退避中的 server：session 已摘除但 lifecycle task 仍存活，
                # 必须一并停止，否则旧配置会把它复活
                connected = name in self._sessions or name in self._stop_events
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
                    detail = extract_exception_detail(exc)
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
            return tool_error(
                f"MCP 工具未找到对应 server: {tool_name}",
                cause=ErrorCause.NOT_FOUND, retryable=False,
                hint="可先调用 list_mcp_servers 查看已连接服务及其工具",
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
            log(f"MCP tool call 失败: {tool_name} → {exc}", "ERROR")
            return error_from_exception(exc, action=f"MCP 调用 {tool_name}")

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
            except Exception:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    log("_signal_stop 异常已忽略", "DEBUG")

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
            detail = extract_exception_detail(exc)
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
            return tool_error(
                f"MCP server '{server_name}' 未连接",
                cause=ErrorCause.STATE, retryable=True,
                hint="服务断开后会自动重连，请稍后重试",
            )

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
            return tool_error(
                f"MCP 工具调用超时 ({timeout}s): {tool_name}",
                cause=ErrorCause.TIMEOUT, retryable=True, code="TOOL_TIMEOUT",
                hint="可缩小参数范围重试，或经 update_mcp_server_config 调大 call_timeout",
            )
        except Exception as first_exc:
            if not _is_connection_closed(first_exc):
                raise
            log(f"MCP server '{server_name}' 连接已断开，尝试重连...", "WARNING")
            if not await self._try_reconnect(server_name):
                return tool_error(
                    f"MCP server '{server_name}' 连接已断开且重连失败",
                    cause=ErrorCause.NETWORK, retryable=True,
                    hint="服务会继续自动重连，请稍后重试",
                )
            with self._lock:
                session = self._sessions.get(server_name)
            if not session:
                return tool_error(
                    f"MCP server '{server_name}' 重连后 session 不可用",
                    cause=ErrorCause.NETWORK, retryable=True,
                    hint="请稍后重试",
                )
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return tool_error(
                    f"MCP 工具调用超时 ({timeout}s): {tool_name}",
                    cause=ErrorCause.TIMEOUT, retryable=True, code="TOOL_TIMEOUT",
                    hint="可缩小参数范围重试，或经 update_mcp_server_config 调大 call_timeout",
                )

        if not hasattr(result, "content"):
            return str(result)
        # 远端 isError 标记恢复为结构化错误信号，供守卫/拦截机制识别
        if getattr(result, "isError", False):
            text = _extract_text_blocks(result)
            return tool_error(
                f"MCP 工具 '{tool_name}' 执行失败: {text or '远端未返回详情'}",
                cause=ErrorCause.INTERNAL, retryable=False,
            )
        return await _render_call_result(result)

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
                except Exception:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        log("_try_reconnect 异常已忽略", "DEBUG")

            with self._lock:
                self._sessions.pop(server_name, None)
                self._stop_events.pop(server_name, None)
                self._lifecycle_tasks.pop(server_name, None)

            self._cleanup_server_entities(server_name)

            count = await self._connect_server(srv)
            log(f"MCP server '{server_name}' 重连成功，发现 {count} 个工具")
            return True
        except Exception as exc:
            detail = extract_exception_detail(exc)
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
                log("_cleanup_server_entities 异常已忽略", "DEBUG")
        try:
            EntityRegistry.unregister(f"mcp:{name}")
        except (KeyError, ValueError):
            log("_cleanup_server_entities 异常已忽略", "DEBUG")

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

        # 等待 session 初始化完成（或失败）；挂死时按超时脱身，
        # lifecycle task 由 stop 信号回收，避免半连接残留
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            stop_event.set()
            with self._lock:
                self._stop_events.pop(srv.name, None)
                self._lifecycle_tasks.pop(srv.name, None)
            self._set_last_error(srv.name, "连接初始化超时")
            raise TimeoutError(f"MCP server '{srv.name}' 初始化超时（30s）") from None

        if result_box and isinstance(result_box[0], Exception):
            with self._lock:
                self._stop_events.pop(srv.name, None)
                self._lifecycle_tasks.pop(srv.name, None)
            self._set_last_error(srv.name, extract_exception_detail(result_box[0]))
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
        首次连接失败直接报错；后续连接断开自动重试（指数退避；连接稳定
        运行超过 _RECONNECT_BUDGET_RESET_SEC 后重试预算复位，偶发抖动
        不会累计耗尽预算导致服务永久死亡）。
        """
        import asyncio

        from mcp import ClientSession

        first_attempt = True
        budget = _RetryBudget(_MAX_LIFECYCLE_RETRIES, _RECONNECT_BUDGET_RESET_SEC)
        connected_at = 0.0
        iteration = 0

        try:
            while not budget.exhausted:
                iteration += 1
                if stop_event.is_set():
                    break
                try:
                    transport_cm = _create_transport(srv)
                    async with transport_cm as streams:
                        read_stream, write_stream = streams[0], streams[1]
                        async with ClientSession(
                            read_stream,
                            write_stream,
                            **self._session_kwargs(srv.name),
                        ) as session:
                            await session.initialize()
                            with self._lock:
                                self._sessions[srv.name] = session
                            connected_at = time.monotonic()

                            if first_attempt:
                                count = await self._register_server_tools(srv, session)
                                result_box.append(count)
                                ready_event.set()
                                first_attempt = False
                            else:
                                self._cleanup_server_entities(srv.name)
                                count = await self._register_server_tools(srv, session)
                                self._set_last_error(srv.name, "")
                                log(f"MCP server '{srv.name}' 自动重连成功 (第 {iteration} 次)，{count} 个工具")

                            await stop_event.wait()
                            return

                except Exception as exc:
                    detail = extract_exception_detail(exc)
                    if first_attempt:
                        log(f"MCP server '{srv.name}' lifecycle 异常: {detail}", "ERROR")
                        result_box.append(exc)
                        ready_event.set()
                        return

                    if stop_event.is_set():
                        return

                    # 重连窗口内被删除/禁用的 server 不再复活
                    current = self._find_server_config(srv.name)
                    if current is None or not current.enabled:
                        log(f"MCP server '{srv.name}' 已删除或禁用，停止重连", tag="MCP")
                        return

                    with self._lock:
                        self._sessions.pop(srv.name, None)
                    self._set_last_error(srv.name, f"连接断开: {detail}")

                    stable = (time.monotonic() - connected_at) if connected_at else 0.0
                    wait = budget.record_failure(stable)
                    log(
                        f"MCP server '{srv.name}' 连接断开: {detail}，"
                        f"{wait}s 后重试 ({budget.attempt}/{_MAX_LIFECYCLE_RETRIES})",
                        "WARNING",
                    )
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=wait)
                        return
                    except asyncio.TimeoutError:
                        pass  # 超时属正常等待结束（正常控制流，非异常）

            if not first_attempt:
                log(f"MCP server '{srv.name}' 重试 {_MAX_LIFECYCLE_RETRIES} 次后放弃", "ERROR")
        finally:
            with self._lock:
                self._sessions.pop(srv.name, None)
                self._stop_events.pop(srv.name, None)
                self._lifecycle_tasks.pop(srv.name, None)
            if not first_attempt:
                self._cleanup_server_entities(srv.name)

    def _session_kwargs(self, server_name: str) -> Dict[str, Any]:
        """构造 ClientSession 关键字参数。

        message_handler（拦截工具列表变更通知）仅在新版 mcp SDK 存在该
        参数时注入——旧版 SDK 无此参数时退回仅 roots 回调，保证兼容。
        """
        kwargs: Dict[str, Any] = {"list_roots_callback": _list_roots_callback}
        try:
            from mcp import ClientSession
            if "message_handler" in inspect.signature(ClientSession.__init__).parameters:
                kwargs["message_handler"] = self._make_message_handler(server_name)
        except Exception:
            log("ClientSession 不支持 message_handler，跳过工具列表热同步", "DEBUG", tag="MCP")
        return kwargs

    def _make_message_handler(self, server_name: str) -> Callable[[Any], Any]:
        """构造注入 ClientSession 的消息处理器（运行在 bridge 事件循环）。

        仅拦截 tools/list_changed 通知安排增量同步；其余消息零干预，
        末尾让出控制权保持 SDK 默认处理器的 checkpoint 语义。
        BaseSession 在接收循环中直接 await 本处理器，异常必须内部吞掉。
        """
        async def _handler(message: Any) -> None:
            import asyncio
            try:
                self._on_session_message(server_name, message)
            except Exception:
                log("MCP session 消息处理异常已忽略", "DEBUG", tag="MCP")
            await asyncio.sleep(0)
        return _handler

    def _on_session_message(self, server_name: str, message: Any) -> None:
        """会话消息分发：工具列表变更 → 防抖登记同步任务。

        SDK 默认把 ToolListChangedNotification 静默丢弃（client/session.py
        的 ``case _: pass``），这里经 message_handler 拦截补上（对齐 dsh
        的 ToolListChanged 重同步）。server 可能连发多条通知，防抖窗口
        内合并为一次同步。
        """
        if not self._is_tool_list_changed(message):
            return
        from core.config import get_config_bool
        if not get_config_bool("mcp_tool_list_sync", True):
            return
        with self._lock:
            if server_name in self._sync_pending:
                return
            self._sync_pending.add(server_name)
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with self._lock:
                self._sync_pending.discard(server_name)
            return
        loop.call_later(_TOOL_SYNC_DEBOUNCE_SEC, self._spawn_tool_sync, server_name)
        log(
            f"MCP server '{server_name}' 通知工具列表变更，"
            f"{_TOOL_SYNC_DEBOUNCE_SEC}s 后同步", "DEBUG", tag="MCP",
        )

    @staticmethod
    def _is_tool_list_changed(message: Any) -> bool:
        """判定消息是否为 server 的工具列表变更通知。"""
        try:
            from mcp import types
        except Exception:
            return False
        return isinstance(message, types.ServerNotification) and isinstance(
            message.root, types.ToolListChangedNotification,
        )

    def _spawn_tool_sync(self, server_name: str) -> None:
        """防抖到期：在 bridge 循环上启动同步任务（call_later 回调）。"""
        import asyncio
        asyncio.ensure_future(self._sync_server_tools(server_name))

    async def _sync_server_tools(self, server_name: str) -> None:
        """按 server 最新 tools/list 增量重同步注册表（增删，不改名）。

        与断线重连的全量重注册互补：server 端动态增删工具（升级/热更新）
        时，注册表不再漂移到下次断线重连。list_tools 后复检会话仍在——
        同步期间 server 断开则放弃本轮（残留注册由 lifecycle 清理路径收走）。
        同名工具的描述/参数变更不在此处理（重连/reload 全量覆盖），
        保持增量最小、避免无谓的 tools 前缀缓存失效。
        """
        try:
            with self._lock:
                session = self._sessions.get(server_name)
            if session is None:
                return
            tools_result = await session.list_tools()
            mcp_tools = list(tools_result.tools)
            with self._lock:
                if server_name not in self._sessions:
                    return  # 同步期间已断开，交还给 lifecycle 清理
                current: Dict[str, str] = {
                    reg: self._tool_original_names.get(reg, reg)
                    for reg, owner in self._tool_server_map.items()
                    if owner == server_name
                }
            new_by_name: Dict[str, Any] = {
                getattr(t, "name", ""): t for t in mcp_tools if getattr(t, "name", "")
            }
            existing = set(current.values())
            removed = [reg for reg, orig in current.items() if orig not in new_by_name]
            added = [t for orig, t in new_by_name.items() if orig not in existing]

            for reg in removed:
                with self._lock:
                    self._tool_server_map.pop(reg, None)
                    self._tool_original_names.pop(reg, None)
                try:
                    EntityRegistry.unregister(reg)
                except (KeyError, ValueError):
                    log("_sync_server_tools 异常已忽略", "DEBUG")
            if added:
                srv = self._find_server_config(server_name)
                call_timeout = srv.call_timeout if srv else _DEFAULT_CALL_TIMEOUT
                self._register_tool_entries(server_name, added, call_timeout=call_timeout)

            # 分组目录与实体工具清单刷新（目录描述反映最新工具列表）
            EntityRegistry.register_group(
                f"mcp:{server_name}",
                self._build_group_description(server_name, mcp_tools),
            )
            mcp_entity = EntityRegistry.get(f"mcp:{server_name}")
            if mcp_entity is not None:
                with self._lock:
                    mcp_entity.meta["tools"] = [
                        reg for reg, owner in self._tool_server_map.items()
                        if owner == server_name
                    ]
            EntityRegistry.bump_version()
            if removed or added:
                log(
                    f"MCP server '{server_name}' 工具列表已同步: "
                    f"+{len(added)} -{len(removed)}",
                    tag="MCP",
                )
        except Exception as exc:
            log(
                f"MCP 工具列表同步失败 ({server_name}): {extract_exception_detail(exc)}",
                "WARNING", tag="MCP",
            )
        finally:
            with self._lock:
                self._sync_pending.discard(server_name)

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
        tool_names = self._register_tool_entries(
            srv.name, mcp_tools,
            call_timeout=getattr(srv, "call_timeout", _DEFAULT_CALL_TIMEOUT),
        )

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
        for t in tools[:_GROUP_DESC_TOOL_LIMIT]:
            t_name = getattr(t, "name", "") or ""
            t_desc = (getattr(t, "description", "") or "").strip().split("\n")[0][:_GROUP_DESC_TOOL_BRIEF_LEN]
            briefs.append(f"{t_name}({t_desc})" if t_desc else t_name)
        suffix = "…" if len(tools) > _GROUP_DESC_TOOL_LIMIT else ""
        desc = f"MCP 服务 {server_name}，工具: {', '.join(briefs)}{suffix}"
        return desc[:_GROUP_DESC_MAX_LEN]

    def _register_tool_entries(
        self,
        server_name: str,
        tools: List[Any],
        call_timeout: Optional[float] = None,
    ) -> List[str]:
        """将 server 的工具批量注册到 EntityRegistry，返回注册名列表。

        工具名与现有实体（内置工具或其他 MCP 工具）冲突时，
        自动加 ``{server}__`` 前缀注册，避免覆盖同名实体。
        注册名统一做供应商函数名约束整形（非法字符替换 + 64 字符上限，
        超限截断并追加短哈希防撞）——OpenAI 风格端点会拒绝含非法字符或
        超长函数名的整个 tools 数组，一个坏名字会导致全会话不可用。
        call_timeout 透传为工具执行超时：MCP 调用常比内置工具慢，
        不传则落入全局默认 60s，会在 bridge 超时（默认 300s）之前被
        执行层提前掐断（用户配置的 call_timeout 变成死配置）。
        默认沉睡策略（mcp_tools_sleep_default）：沉睡组不驻留 schema，
        显著缩小 tools 前缀（缓存友好），AI 需要时 activate_tool_group 唤醒。
        """
        sleep = _mcp_sleep_enabled(server_name)
        sleep_brief = f"MCP 服务 {server_name}（{len(tools)} 个工具）" if sleep else ""
        meta: Optional[Dict[str, Any]] = None
        if call_timeout is not None and call_timeout > 0:
            meta = {"timeout": float(call_timeout)}
        registered: List[str] = []
        for t in tools:
            t_name, t_params = _parse_mcp_tool(t)
            bridge = self

            reg_name = _sanitize_tool_name(t_name)
            if EntityRegistry.exists(reg_name):
                prefixed = _sanitize_tool_name(f"{server_name}__{t_name}")
                log(
                    f"MCP 工具名冲突: '{reg_name}' 已被占用，"
                    f"server '{server_name}' 的工具注册为 '{prefixed}'",
                    "WARNING",
                )
                reg_name = prefixed

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
                allow_sleep=sleep,
                sleep_brief=sleep_brief,
                meta=meta,
            )
            with self._lock:
                self._tool_server_map[reg_name] = server_name
                if reg_name != t_name:
                    self._tool_original_names[reg_name] = t_name
            registered.append(reg_name)
        return registered


# 全局单例
_mcp_bridge: Optional[MCPBridge] = None


def get_mcp_bridge() -> Optional[MCPBridge]:
    return _mcp_bridge


def set_mcp_bridge(bridge: MCPBridge) -> None:
    global _mcp_bridge
    _mcp_bridge = bridge
