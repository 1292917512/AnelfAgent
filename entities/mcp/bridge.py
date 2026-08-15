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

import functools
import hashlib
import inspect
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.entity import EntityMetadata, EntityRegistry, EntityType, ToolParam
from core.log import log
from core.path import ConfigPaths
from core.sanitizer import is_sanitize_enabled, sanitize_text
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import coerce_bool_arg

_MAX_LIFECYCLE_RETRIES = 5
_DEFAULT_CALL_TIMEOUT = 300.0
# OpenAI function-calling 工具名上限：超限会导致供应商拒绝整个 tools 数组
_MAX_TOOL_NAME_LEN = 64
# 单次 MCP 工具结果允许注入的图片数上限（防上下文膨胀）
_MAX_RESULT_IMAGES = 4
# 单张注入图片的解码字节上限（防超大图灌满磁盘与多模态管道）
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
# 工具列表变更通知的同步防抖（秒）：server 可能连发多条通知，合并为一次同步
_TOOL_SYNC_DEBOUNCE_SEC = 1.0
# 连接稳定运行该时长后，重连重试预算复位（秒）：
# 长期运行的服务偶发抖动不应累计耗尽预算而永久死亡（对齐 dsh 稳定窗口复位）
_RECONNECT_BUDGET_RESET_SEC = 300.0

# 分组目录描述的截断上限
_GROUP_DESC_TOOL_LIMIT = 8        # 描述中列出的工具数量上限
_GROUP_DESC_TOOL_BRIEF_LEN = 40   # 单个工具一句话描述截断长度
_GROUP_DESC_MAX_LEN = 300         # 描述整体长度上限
# 工具结果 JSON 中返回的工具名列表上限
_TOOL_RESULT_LIST_LIMIT = 50

# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_MCP_CONFIGS = {
    "entity/mcp": {
        "mcp_stdio_passthrough_env": {
            "description": "是否向 stdio 子进程透传全量环境变量（默认仅白名单）",
            "default": False,
        },
        "mcp_tools_sleep_default": {
            "description": "是否默认沉睡 MCP 服务工具组（需要时调 activate_tool_group 唤醒；缩小 tools 前缀提升缓存命中）",
            "default": True,
        },
        "mcp_sleep_excludes": {
            "description": "不沉睡的 MCP 服务名单（逗号分隔的服务名，如 git,excel；高频使用的服务可常驻）",
            "default": "",
        },
        "tool_activation_sticky": {
            "description": "是否让激活的分组保持粘性不再过期沉睡（避免激活/过期反复重写缓存前缀）",
            "default": True,
        },
        "mcp_tool_list_sync": {
            "description": "收到 server 的工具列表变更通知时自动重同步注册"
                           "（关闭则仅在重连/手动 reload 时刷新）",
            "default": True,
        },
        "mcp_image_passthrough": {
            "description": "MCP 工具返回的图片落盘并经多模态约定注入"
                           "（视觉模型可直接看到截图；关闭则仅保留文本占位）",
            "default": True,
        },
    },
}


def _server_stay_awake(server_name: str) -> bool:
    """读取 mcp_servers.json 中该服务的 stay_awake 覆盖（每服务常驻开关）。"""
    try:
        import json as _json
        import os as _os

        from core.path import ConfigPaths
        path = ConfigPaths.MCP_SERVERS
        if not _os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        cfg = (data.get("mcpServers") or {}).get(server_name) or {}
        return bool(cfg.get("stay_awake"))
    except Exception:
        return False


def _mcp_sleep_enabled(server_name: str) -> bool:
    """该 MCP 服务的工具组是否沉睡。

    优先级：每服务 stay_awake 覆盖（mcp_servers.json）> 全局排除名单 > 全局默认。
    """
    from core.config import get_config, get_config_bool
    if not get_config_bool("mcp_tools_sleep_default", True):
        return False
    if _server_stay_awake(server_name):
        return False
    excludes = str(get_config("mcp_sleep_excludes", "") or "")
    excluded = {s.strip() for s in excludes.split(",") if s.strip()}
    return server_name not in excluded


def apply_sleep_policy(server_name: str) -> bool:
    """按当前策略刷新某服务已注册工具的沉睡标记（stay_awake 切换后即时生效）。

    就地更新实体 meta 并推进注册表版本（无需重连/重启，
    下一个会话的工具集装配自动应用新策略）。
    """
    sleep = _mcp_sleep_enabled(server_name)
    group = f"mcp:{server_name}"
    tools = [
        e for e in EntityRegistry.get_by_group(group)
        if e.entity_type == EntityType.TOOL
    ]
    if not tools:
        return False
    brief = f"MCP 服务 {server_name}（{len(tools)} 个工具）"
    for e in tools:
        e.meta["allow_sleep"] = sleep
        e.meta["sleep_brief"] = brief if sleep else ""
    EntityRegistry.bump_version()
    log(
        f"MCP 沉睡策略已应用: {server_name} → {'沉睡' if sleep else '常驻'} ({len(tools)} 工具)",
        tag="MCP",
    )
    return True

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_MCP_CONFIGS)


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
        provider = get_config_provider()
        # 使用公开属性 BotConfigProvider.config；getattr 兜底防止 agent 层接口变动，
        # 待 agent 层提供 mcp_config_path 专用公开 API 后可进一步收敛
        bot_config = getattr(provider, "config", None)
        mcp_path = getattr(bot_config, "mcp_config_path", "") if bot_config is not None else ""
        if mcp_path:
            p = Path(mcp_path)
            if p.exists():
                return str(p)
    except Exception as e:
        log(f"MCP 配置路径获取失败: {e}", "DEBUG")
    for c in [Path(ConfigPaths.MCP_SERVERS), Path("mcp_servers.json")]:
        if c.exists():
            return str(c)
    return None


def _parse_mcp_data(data: Dict[str, Any]) -> List[MCPServerConfig]:
    """从 JSON dict 解析 server 列表（兼容 mcpServers 包装格式和旧版 servers 列表格式）。"""
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


class _RetryBudget:
    """重连重试预算：连续失败计数，稳定连接超过窗口后复位。

    对齐 dsh reconnect 的 budget-reset-after-stability：没有复位时，
    长期运行的服务偶发抖动累计 N 次后永久放弃（工具全部注销），
    需要人工 reload 才能恢复——稳定期过后的失败应重新计满预算。
    """

    def __init__(self, max_retries: int, reset_after_sec: float) -> None:
        self._max = max_retries
        self._reset_after = reset_after_sec
        self.attempt = 0

    @property
    def exhausted(self) -> bool:
        return self.attempt >= self._max

    def record_failure(self, stable_seconds: float = 0.0) -> float:
        """记录一次失败，返回退避等待秒数。

        stable_seconds 为该次失败前连接的稳定运行时长；达到复位窗口时
        预算清零重计（本次失败即新预算的第 1 次），退避也从最短档重来。
        """
        if stable_seconds >= self._reset_after:
            self.attempt = 0
            log(
                f"连接稳定运行 {int(stable_seconds)}s 后失败，重连重试预算已复位",
                "DEBUG", tag="MCP",
            )
        self.attempt += 1
        return float(min(2 ** (self.attempt - 1), 60))


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
            text = self._extract_text_blocks(result)
            return tool_error(
                f"MCP 工具 '{tool_name}' 执行失败: {text or '远端未返回详情'}",
                cause=ErrorCause.INTERNAL, retryable=False,
            )
        return await self._render_call_result(result)

    async def _render_call_result(self, result: Any) -> str:
        """MCP CallToolResult → 工具结果文本。

        内容块分派（对齐 dsh 的保真转换，图片更进一步直传）：
        - text 块按序拼接；
        - image 块落盘为文件并经 ``_multimodal`` 约定返回——视觉模型在
          think_loop 侧直接"看到"图片（chrome-devtools/playwright 截图场景），
          非视觉模型读文本占位中的路径（可经 recognize_image 读取）；
          base64 原文绝不进入上下文（此前 str(item) 会倾倒整段 pydantic repr）；
        - audio/resource 块以短占位说明（防二进制灌入上下文）；
        - structuredContent 在无任何文本时兜底输出 JSON。

        模型可见性：无图片时输出纯文本（与旧版一致）；有图片时输出
        ``{"_multimodal": true, "text": ..., "images": [...]}`` JSON，由
        think_loop._append_multimodal_result 消费注入，注入位置在对话尾部
        user 消息，不影响缓存前缀。
        """
        import asyncio

        from core.config import get_config_bool

        passthrough = get_config_bool("mcp_image_passthrough", True)
        text_parts: List[str] = []
        placeholders: List[str] = []
        image_paths: List[str] = []
        dropped_images = 0

        for item in list(getattr(result, "content", None) or []):
            btype = getattr(item, "type", "")
            if btype == "text" or (not btype and hasattr(item, "text")):
                text_parts.append(str(getattr(item, "text", "") or ""))
            elif btype == "image":
                if not passthrough:
                    dropped_images += 1
                    continue
                data = str(getattr(item, "data", "") or "")
                mime = str(getattr(item, "mimeType", "") or "image/png")
                # 落盘放工作线程，避免大 base64 解码阻塞 bridge 事件循环
                path = await asyncio.to_thread(self._save_mcp_image, data, mime)
                if path and len(image_paths) < _MAX_RESULT_IMAGES:
                    image_paths.append(path)
                elif path:
                    dropped_images += 1
                else:
                    placeholders.append(f"[image: {mime}，数据解码失败或过大已丢弃]")
            elif btype == "audio":
                kb = len(str(getattr(item, "data", "") or "")) // 1024
                placeholders.append(
                    f"[audio: {getattr(item, 'mimeType', '音频')}，约 {kb}KB 音频数据已丢弃]"
                )
            elif btype == "resource_link":
                placeholders.append(f"[resource: {getattr(item, 'uri', '')}]")
            elif btype == "resource":
                placeholders.append(self._render_embedded_resource(item))
            else:
                placeholders.append(f"[未知内容块: {btype or type(item).__name__}]")

        if dropped_images:
            placeholders.append(
                f"[另有 {dropped_images} 张图片未注入"
                f"（超出单次上限 {_MAX_RESULT_IMAGES} 或已关闭 mcp_image_passthrough）]"
            )

        text = "\n".join(p for p in text_parts if p)
        if placeholders:
            text = (text + "\n" if text else "") + "\n".join(placeholders)

        structured = getattr(result, "structuredContent", None)
        if not text and isinstance(structured, dict) and structured:
            return json.dumps(structured, ensure_ascii=False)

        if image_paths:
            return json.dumps({
                "_multimodal": True,
                "text": text or "[系统] 上方工具返回了图片，请查看后继续。",
                "images": image_paths,
            }, ensure_ascii=False)
        return text

    @staticmethod
    def _extract_text_blocks(result: Any) -> str:
        """提取结果中的全部 text 块（isError 路径用，不含占位与图片）。"""
        parts: List[str] = []
        for item in list(getattr(result, "content", None) or []):
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts)

    @staticmethod
    def _render_embedded_resource(item: Any) -> str:
        """EmbeddedResource 块 → 文本提取或占位（blob 不进上下文）。"""
        res = getattr(item, "resource", None)
        uri = str(getattr(res, "uri", "") or "") if res is not None else ""
        if res is not None and getattr(res, "text", None):
            return f"[resource: {uri}]\n{res.text}"
        if res is not None and getattr(res, "blob", None):
            kb = len(str(res.blob)) // 1024
            return f"[resource: {uri}，二进制内容约 {kb}KB 已丢弃]"
        return f"[resource: {uri}]"

    @staticmethod
    def _save_mcp_image(data_b64: str, mime_type: str) -> Optional[str]:
        """base64 图片落盘到 uploads/mcp/，返回路径；空数据/超大/解码失败返回 None。"""
        import base64
        import uuid as _uuid

        try:
            raw = base64.b64decode(data_b64 or "", validate=False)
        except Exception:
            return None
        if not raw or len(raw) > _MAX_IMAGE_BYTES:
            return None
        ext = "jpg" if "jpeg" in mime_type else mime_type.split("/")[-1] if "/" in mime_type else "png"
        ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8] or "png"
        folder = Path(ConfigPaths.UPLOAD_DIR) / "mcp"
        folder.mkdir(parents=True, exist_ok=True)
        fname = f"mcp_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:6]}.{ext}"
        (folder / fname).write_bytes(raw)
        return str(folder / fname)

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

        # 等待 session 初始化完成（或失败）
        await ready_event.wait()

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
                    transport_cm = self._create_transport(srv)
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
            t_name, t_params = self._parse_mcp_tool(t)
            bridge = self

            reg_name = self._sanitize_tool_name(t_name)
            if EntityRegistry.exists(reg_name):
                prefixed = self._sanitize_tool_name(f"{server_name}__{t_name}")
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

    @staticmethod
    def _sanitize_tool_name(name: str) -> str:
        """注册名整形为供应商合法的函数名（[A-Za-z0-9_-]{1,64}）。

        非法字符（server 名含点号等）替换为下划线；超长截断并追加
        注册名 SHA-256 前 8 位十六进制防截断撞名。整形后名字变化时，
        原始名由调用方的 ``_tool_original_names`` 映射兜底还原。
        """
        sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", name)
        if len(sanitized) > _MAX_TOOL_NAME_LEN:
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
            sanitized = f"{sanitized[:_MAX_TOOL_NAME_LEN - 9]}_{digest}"
        return sanitized

    # stdio 子进程环境变量白名单（防止敏感 env 泄露给第三方 MCP server）
    _STDIO_ENV_WHITELIST = frozenset({
        "PATH", "HOME", "USER", "LOGNAME", "SHELL",
        "LANG", "LANGUAGE", "TERM", "TZ",
        "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
        "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "HOMEDRIVE", "HOMEPATH", "PATHEXT", "USERNAME", "OS",
    })

    @staticmethod
    def _build_stdio_env(user_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构建 stdio 子进程环境变量：默认白名单 + 用户显式配置。

        配置 mcp_stdio_passthrough_env=True 时恢复全量透传。
        """
        try:
            from core.config import ConfigManager
            passthrough = bool(ConfigManager.get("mcp_stdio_passthrough_env", False))
        except Exception:
            passthrough = False

        if passthrough:
            env: Dict[str, str] = dict(os.environ)
        else:
            env = {
                k: v for k, v in os.environ.items()
                if k in MCPBridge._STDIO_ENV_WHITELIST or k.startswith("LC_")
            }

        env["ANELF_MCP_STDIO"] = "1"
        env["ANELF_LOG_STREAM"] = "stderr"
        env["PYTHONUNBUFFERED"] = "1"
        if user_env:
            env.update(user_env)
        return env

    @staticmethod
    def _create_transport(srv: MCPServerConfig) -> Any:
        """根据配置创建传输上下文管理器。"""
        transport = srv.transport or ("stdio" if srv.command else "streamable_http")

        if transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client
            stdio_env = MCPBridge._build_stdio_env(srv.env)
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
        """解析 MCP Tool 对象为名称和参数列表。

        参数 schema 保真：type 缺失的联合类型写法（anyOf/oneOf，MCP server
        表达可选参数的惯用法）解引用取非 null 分支；default/items 等
        附加键经 schema_extra 直通 wire schema——模型由此看到默认值与
        数组元素结构，而不是被静默丢弃后按 string 兜底猜参数。
        """
        name = mcp_tool.name
        params: List[ToolParam] = []
        input_schema = getattr(mcp_tool, "inputSchema", None) or {}
        if isinstance(input_schema, dict):
            properties = input_schema.get("properties", {})
            required_list = input_schema.get("required", [])
            for p_name, p_schema in properties.items():
                if not isinstance(p_schema, dict):
                    params.append(ToolParam(name=p_name, required=p_name in required_list))
                    continue
                p_type, schema_extra = MCPBridge._parse_param_schema(p_schema)
                params.append(ToolParam(
                    name=p_name,
                    description=p_schema.get("description", ""),
                    type=p_type,
                    required=p_name in required_list,
                    enum=p_schema.get("enum"),
                    schema_extra=schema_extra or None,
                ))
        return name, params

    @staticmethod
    def _parse_param_schema(p_schema: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """解析单个参数的 JSON Schema 片段为 (type, schema_extra)。

        - type 缺失而 anyOf/oneOf 存在 → 取首个非 null 分支的 type；
        - default/items/数值范围等附加键保留进 schema_extra（随 ToolParam
          直通 wire schema 的 properties 字段）。
        """
        extra: Dict[str, Any] = {}
        p_type = str(p_schema.get("type", "") or "")
        if not p_type:
            for union_key in ("anyOf", "oneOf"):
                union = p_schema.get(union_key)
                if isinstance(union, list):
                    for branch in union:
                        if (
                            isinstance(branch, dict)
                            and branch.get("type")
                            and branch.get("type") != "null"
                        ):
                            p_type = str(branch["type"])
                            break
                if p_type:
                    break
        if not p_type:
            p_type = "string"  # 保持既有兜底行为
        for key in ("default", "items", "minimum", "maximum", "pattern", "format"):
            if key in p_schema:
                extra[key] = p_schema[key]
        return p_type, extra


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
        from services import MCPService
        svc = MCPService()
        await asyncio.to_thread(svc.set_server_enabled, server_name, enabled, reload=False)
    except Exception as inner_exc:
        log(f"同步 enabled 状态失败({action}): {inner_exc}", "DEBUG", tag="mcp")


async def _do_connect_server(bridge: MCPBridge, server_name: str, action: str) -> Dict[str, Any]:
    """连接 MCP server 并同步 enabled=True（connect/toggle 工具共用的启停实现）。

    services/mcp.py 的 toggle_server 存在平行实现，由 services 侧负责人另行收敛，
    本模块不 import services 的实现以遵守依赖方向。
    """
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

    from services import MCPService

    svc = MCPService()
    result = await asyncio.to_thread(
        svc.update_server_config,
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

    from services import MCPService

    svc = MCPService()
    result = await asyncio.to_thread(
        svc.set_server_enabled,
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

    from services import MCPService
    svc = MCPService()
    data = svc.load_config()
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
        svc.update_server_config,
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


@mcp_tool_call(require_bridge=True)
async def _tool_reload_mcp_config(bridge: MCPBridge) -> str:
    """手动触发 MCP 配置热重载。"""
    import asyncio
    result = await asyncio.to_thread(bridge.reload_config)
    return json.dumps({"success": True, "reload": result}, ensure_ascii=False)
