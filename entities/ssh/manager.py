"""SSH 连接管理器 — 连接池、会话复用、状态广播。

基于 asyncssh 的结构化执行：
- connect() 双重检查锁建连（per-name Lock），keepalive 死链检测
- connection_lost 回调置状态并广播（前端 SSE 实时刷新）
- execute() 两阶段执行：通道打开失败（命令未开始，典型为池内失效连接）
  自动重连重试一次；命令执行中断开则抛 SshCommandInterrupted 交调用方决策
- upload/download 走 SFTP

状态变更经模块级订阅者队列广播，router.py 的 SSE 端点消费。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import asyncssh

from core.config import expand_env_refs, get_config_bool, get_config_int
from core.log import log

from .store import SshConfigStore, get_ssh_store

# 连接状态常量
STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"

# 工具结果输出截断上限（字符数）
_OUTPUT_LIMIT = 8000
# 默认命令超时（秒），可被实体配置 ssh_default_timeout 覆盖
_FALLBACK_CMD_TIMEOUT = 60.0

# SSE 订阅者队列（router.py 注册），广播失败仅告警不中断
_subscribers: List[asyncio.Queue] = []


class SshCommandInterrupted(Exception):
    """命令执行中 SSH 连接断开：命令已在远端启动，但执行结果未知。

    与会话建立阶段的失败不同，这类中断无法安全地自动重试——目标可能
    正在关机/重启（重连注定失败白等 connect 超时），且重跑整条命令会
    放大副作用与耗时。重试与否的决策交还调用方（AI/Web）。
    """

    def __init__(self, connection: str, reason: str, elapsed_ms: int) -> None:
        self.connection = connection
        self.reason = reason
        self.elapsed_ms = elapsed_ms
        super().__init__(
            f"连接 {connection} 在命令执行中（已运行 {elapsed_ms // 1000}s）断开: {reason}"
        )


def subscribe_status() -> asyncio.Queue:
    """注册状态变更订阅者队列（SSE 端点调用）。"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    _subscribers.append(queue)
    return queue


def unsubscribe_status(queue: asyncio.Queue) -> None:
    """注销状态变更订阅者队列。"""
    if queue in _subscribers:
        _subscribers.remove(queue)


class ManagedConnection:
    """单个 SSH 连接的生命周期状态。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.conn: Optional[asyncssh.SSHClientConnection] = None
        self.status: str = STATUS_DISCONNECTED
        self.last_error: str = ""
        self.connected_at: int = 0
        self.last_used_at: int = 0

    def snapshot(self, profile: Optional[Dict[str, Any]] = None, is_default: bool = False) -> Dict[str, Any]:
        """生成状态快照（供 API / 上下文注入，不含凭据）。"""
        return {
            "name": self.name,
            "host": profile.get("host", "") if profile else "",
            "port": profile.get("port", 22) if profile else 22,
            "username": profile.get("username", "") if profile else "",
            "description": profile.get("description", "") if profile else "",
            "status": self.status,
            "last_error": self.last_error,
            "connected_at": self.connected_at,
            "last_used_at": self.last_used_at,
            "is_default": is_default,
        }


def _broadcast_status(manager: "SshConnectionManager", name: str, event: str) -> None:
    """向所有订阅者推送状态变更事件。"""
    snapshot = manager.get_snapshot(name)
    if snapshot is None:
        return
    payload = {"event": event, "name": name, "connection": snapshot}
    for queue in list(_subscribers):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            log("SSH 状态订阅队列已满，丢弃事件", "DEBUG", tag="SSH")


class _ClientHandler(asyncssh.SSHClient):
    """连接事件处理器：connection_lost 时置状态并广播。"""

    def __init__(self, manager: "SshConnectionManager", name: str) -> None:
        self._manager = manager
        self._name = name

    def connection_lost(self, exc: Optional[Exception]) -> None:
        managed = self._manager._connections.get(self._name)
        if managed is None or managed.status == STATUS_DISCONNECTED:
            return
        if exc is None:
            managed.status = STATUS_DISCONNECTED
            managed.last_error = ""
        else:
            managed.status = STATUS_ERROR
            managed.last_error = str(exc)[:200]
            log(f"SSH 连接异常断开: {self._name} - {exc}", "WARNING", tag="SSH")
        managed.conn = None
        _broadcast_status(self._manager, self._name, "status")


class SshConnectionManager:
    """SSH 连接池管理器（进程内单例，经 get_ssh_manager() 获取）。"""

    def __init__(self, store: Optional[SshConfigStore] = None) -> None:
        self._store = store or get_ssh_store()
        self._connections: Dict[str, ManagedConnection] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_lock(self, name: str) -> asyncio.Lock:
        lock = self._locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[name] = lock
        return lock

    def _managed(self, name: str) -> ManagedConnection:
        managed = self._connections.get(name)
        if managed is None:
            managed = ManagedConnection(name)
            self._connections[name] = managed
        return managed

    def get_snapshot(self, name: str) -> Optional[Dict[str, Any]]:
        """返回指定连接的状态快照（含配置元信息，不含凭据）。"""
        profile = self._store.get(name)
        if profile is None and name not in self._connections:
            return None
        managed = self._managed(name)
        return managed.snapshot(profile, is_default=(self._store.get_default_name() == name))

    def list_statuses(self) -> List[Dict[str, Any]]:
        """返回所有已配置连接的状态快照列表。"""
        default_name = self._store.get_default_name()
        results: List[Dict[str, Any]] = []
        for profile in self._store.list_profiles():
            name = str(profile["name"])
            managed = self._managed(name)
            results.append(managed.snapshot(profile, is_default=(name == default_name)))
        return results

    def resolve_name(self, name: str) -> str:
        """解析目标连接名：空则用默认连接，无配置抛 ValueError（对外公开）。"""
        return self._resolve_name(name)

    def _resolve_name(self, name: str) -> str:
        """解析目标连接名：空则用默认连接，无配置抛 ValueError。"""
        target = name.strip() if name else self._store.get_default_name()
        if not target:
            raise ValueError("未配置任何 SSH 连接，请先通过 ssh_add 或 Web 面板添加")
        return target

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def connect(self, name: str) -> Dict[str, Any]:
        """建立连接（已连接则直接返回），返回状态快照。"""
        profile = self._store.get(name)
        if profile is None:
            raise ValueError(f"连接不存在: {name}")

        managed = self._managed(name)
        if managed.status == STATUS_CONNECTED and managed.conn is not None:
            return managed.snapshot(profile, self._store.get_default_name() == name)

        lock = self._get_lock(name)
        async with lock:
            # 双重检查：等锁期间可能已被其他协程建连
            if managed.status == STATUS_CONNECTED and managed.conn is not None:
                return managed.snapshot(profile, self._store.get_default_name() == name)

            managed.status = STATUS_CONNECTING
            managed.last_error = ""
            _broadcast_status(self, name, "status")
            try:
                conn = await self._create_connection(profile)
            except Exception as exc:
                managed.status = STATUS_ERROR
                managed.last_error = str(exc)[:200]
                _broadcast_status(self, name, "status")
                log(f"SSH 连接失败: {name}@{profile.get('host')} - {exc}", "WARNING", tag="SSH")
                raise ConnectionError(f"连接 {name} 失败: {exc}") from exc

            managed.conn = conn
            managed.status = STATUS_CONNECTED
            managed.connected_at = int(time.time() * 1000)
            managed.last_used_at = managed.connected_at
            _broadcast_status(self, name, "status")
            log(f"SSH 已连接: {name} ({profile.get('username')}@{profile.get('host')}:{profile.get('port')})", tag="SSH")
            return managed.snapshot(profile, self._store.get_default_name() == name)

    async def _create_connection(self, profile: Dict[str, Any]) -> asyncssh.SSHClientConnection:
        """根据配置建立 asyncssh 连接（展开 ${ENV_VAR} 引用）。"""
        password = str(expand_env_refs(profile.get("password", "")))
        passphrase = str(expand_env_refs(profile.get("passphrase", ""))) or None
        key_path = str(profile.get("key_path", "")).strip()
        key_path = os.path.expanduser(key_path) if key_path else ""
        if key_path and not os.path.exists(key_path):
            raise FileNotFoundError(f"私钥文件不存在: {key_path}")

        keepalive = get_config_int("ssh_keepalive_interval", 30)

        kwargs: Dict[str, Any] = {
            "username": profile.get("username", ""),
            "keepalive_interval": keepalive,
            "keepalive_count_max": 3,
            "encoding": "utf-8",
            "errors": "replace",
            "client_factory": lambda: _ClientHandler(self, str(profile["name"])),
        }
        # 默认信任所有主机（known_hosts=None，适合内网/自管服务器）；
        # 开启校验时不传该参数，走 asyncssh 默认行为（读取 ~/.ssh/known_hosts）
        if not get_config_bool("ssh_verify_host_key", False):
            kwargs["known_hosts"] = None
        if key_path:
            kwargs["client_keys"] = [key_path]
            if passphrase:
                kwargs["passphrase"] = passphrase
        elif password:
            kwargs["password"] = password

        return await asyncio.wait_for(
            asyncssh.connect(str(profile["host"]), int(profile.get("port", 22)), **kwargs),
            timeout=get_config_int("ssh_connect_timeout", 15),
        )

    async def disconnect(self, name: str) -> None:
        """主动断开连接（未连接则无操作）。"""
        managed = self._connections.get(name)
        if managed is None or managed.conn is None:
            if managed is not None:
                managed.status = STATUS_DISCONNECTED
                managed.last_error = ""
            return
        conn, managed.conn = managed.conn, None
        managed.status = STATUS_DISCONNECTED
        managed.last_error = ""
        try:
            conn.close()
            await conn.wait_closed()
        except Exception as exc:
            log(f"SSH 关闭连接异常（已忽略）: {name} - {exc}", "DEBUG", tag="SSH")
        _broadcast_status(self, name, "status")
        log(f"SSH 已断开: {name}", tag="SSH")

    async def close_all(self) -> None:
        """关闭所有连接（进程退出时由 Lifecycle 调用）。"""
        for name in list(self._connections):
            await self.disconnect(name)

    # ------------------------------------------------------------------
    # 命令执行与文件传输
    # ------------------------------------------------------------------

    async def _ensure_connected(self, name: str) -> asyncssh.SSHClientConnection:
        """获取活跃连接，未连接则建连。"""
        managed = self._managed(name)
        if managed.status == STATUS_CONNECTED and managed.conn is not None:
            return managed.conn
        await self.connect(name)
        managed = self._managed(name)
        assert managed.conn is not None
        return managed.conn

    async def execute(
        self,
        command: str,
        name: str = "",
        timeout: float = 0,
        work_dir: str = "",
    ) -> Dict[str, Any]:
        """在指定（或默认）连接上执行命令，返回结构化结果。

        两阶段语义决定断线时的处理：
        - 通道打开失败：命令尚未开始（典型为连接池内的失效连接），
          重置状态、重建连接后完整重试一次；
        - 执行中断开：命令已被中途掐断、结果不可知，抛
          SshCommandInterrupted 立即返回，不重试。

        Returns:
            {"ok", "exit_code", "stdout", "stderr", "connection", "truncated"}

        Raises:
            SshCommandInterrupted: 命令执行中连接断开（含对端关机/网络中断）。
        """
        target = self._resolve_name(name)
        conn = await self._ensure_connected(target)
        effective_timeout = timeout or get_config_int("ssh_default_timeout", int(_FALLBACK_CMD_TIMEOUT))
        full_command = f"cd {work_dir} && {command}" if work_dir.strip() else command

        def _mark_disconnected() -> None:
            managed = self._managed(target)
            managed.conn = None
            managed.status = STATUS_DISCONNECTED
            _broadcast_status(self, target, "status")

        # 阶段一：打开执行通道。此阶段断线 = 命令未开始，重试安全。
        # create_process 拆分自 conn.run：以"命令是否已在远端启动"为界
        # 区分可重试与不可重试，是断线路由的唯一依据。
        try:
            process = await conn.create_process(full_command)
        except (asyncssh.ConnectionLost, asyncssh.DisconnectError) as exc:
            log(f"SSH 会话打开失败，重连后重试: {target} - {exc}", "DEBUG", tag="SSH")
            _mark_disconnected()
            try:
                conn = await self._ensure_connected(target)
                process = await conn.create_process(full_command)
            except (asyncssh.ConnectionLost, asyncssh.DisconnectError) as retry_exc:
                _mark_disconnected()
                raise ConnectionError(f"重连后仍无法建立会话: {retry_exc}") from retry_exc

        # 阶段二：等待命令完成。此阶段断线 = 命令被中途掐断，立即上抛。
        started = time.monotonic()
        try:
            result = await process.wait(timeout=effective_timeout)
        except asyncio.TimeoutError:
            # 关闭通道终止远端会话，防超时命令在池化连接上继续滞留
            process.close()
            raise
        except (asyncssh.ConnectionLost, asyncssh.DisconnectError) as exc:
            _mark_disconnected()
            raise SshCommandInterrupted(
                connection=target,
                reason=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ) from exc

        self._managed(target).last_used_at = int(time.time() * 1000)
        stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", errors="replace")
        truncated = len(stdout) > _OUTPUT_LIMIT or len(stderr) > _OUTPUT_LIMIT
        exit_code = result.returncode if result.returncode is not None else -1
        return {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "stdout": stdout[:_OUTPUT_LIMIT],
            "stderr": stderr[:_OUTPUT_LIMIT // 2],
            "connection": target,
            "truncated": truncated,
        }

    async def upload(self, local_path: str, remote_path: str, name: str = "") -> Dict[str, Any]:
        """经 SFTP 上传本地文件到远程。"""
        target = self._resolve_name(name)
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"本地文件不存在: {local_path}")
        conn = await self._ensure_connected(target)
        sftp = await conn.start_sftp_client()
        try:
            await sftp.put(local_path, remote_path)
        finally:
            sftp.exit()
        self._managed(target).last_used_at = int(time.time() * 1000)
        size = os.path.getsize(local_path)
        log(f"SSH 上传完成: {local_path} → {target}:{remote_path} ({size} bytes)", tag="SSH")
        return {"ok": True, "connection": target, "local_path": local_path,
                "remote_path": remote_path, "size": size}

    async def download(self, remote_path: str, local_path: str, name: str = "") -> Dict[str, Any]:
        """经 SFTP 下载远程文件到本地。"""
        target = self._resolve_name(name)
        local_dir = os.path.dirname(os.path.abspath(local_path))
        os.makedirs(local_dir, exist_ok=True)
        conn = await self._ensure_connected(target)
        sftp = await conn.start_sftp_client()
        try:
            await sftp.get(remote_path, local_path)
        finally:
            sftp.exit()
        self._managed(target).last_used_at = int(time.time() * 1000)
        size = os.path.getsize(local_path) if os.path.exists(local_path) else 0
        log(f"SSH 下载完成: {target}:{remote_path} → {local_path} ({size} bytes)", tag="SSH")
        return {"ok": True, "connection": target, "remote_path": remote_path,
                "local_path": local_path, "size": size}

    # ------------------------------------------------------------------
    # 配置变更（委托 store，清理关联连接状态）
    # ------------------------------------------------------------------

    async def forget(self, name: str) -> None:
        """断开连接并移出连接池（不改动 store 配置，供重命名等场景）。"""
        await self.disconnect(name)
        self._connections.pop(name, None)
        self._locks.pop(name, None)

    async def remove_profile(self, name: str) -> bool:
        """删除连接配置并关闭对应连接。"""
        await self.forget(name)
        return await self._store.delete(name)


_manager_instance: Optional[SshConnectionManager] = None


def get_ssh_manager() -> SshConnectionManager:
    """获取 SshConnectionManager 单例。"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = SshConnectionManager()
    return _manager_instance
