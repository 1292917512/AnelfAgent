"""SshConnectionManager 连接池与执行链路测试（mock asyncssh）。

覆盖：名称解析、连接复用、结构化执行、两阶段断线路由
（打开失败重连重试 / 执行中断立即抛错）、SFTP 传输、状态快照。
"""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, patch

import asyncssh
import pytest

from entities.ssh.manager import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    SshCommandInterrupted,
    SshConnectionManager,
)
from entities.ssh.store import SshConfigStore


class FakeResult:
    """模拟 asyncssh.SSHCompletedProcess。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class OpenError:
    """会话打开阶段抛出的异常（命令未开始）。"""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


class WaitError:
    """命令执行阶段抛出的异常（命令已在远端启动）。"""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


class FakeProcess:
    """模拟 asyncssh.SSHClientProcess。"""

    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.closed = False

    async def wait(self, timeout: float | None = None) -> Any:
        if isinstance(self._outcome, WaitError):
            raise self._outcome.exc
        return self._outcome

    def close(self) -> None:
        self.closed = True


class FakeSftp:
    def __init__(self) -> None:
        self.put_calls: List[Any] = []
        self.get_calls: List[Any] = []
        self.exited = False

    async def put(self, local: str, remote: str) -> None:
        self.put_calls.append((local, remote))

    async def get(self, remote: str, local: str) -> None:
        self.get_calls.append((remote, local))

    def exit(self) -> None:
        self.exited = True


class FakeConn:
    """模拟 asyncssh.SSHClientConnection（create_process + wait 两阶段）。"""

    def __init__(self, run_outcomes: List[Any]) -> None:
        # 每个元素：FakeResult（成功）/ OpenError（打开失败）/ WaitError（执行中失败）
        self._run_outcomes = list(run_outcomes)
        self.run_commands: List[str] = []
        self.processes: List[FakeProcess] = []
        self.sftp = FakeSftp()
        self.closed = False

    async def create_process(self, command: str) -> FakeProcess:
        self.run_commands.append(command)
        outcome = self._run_outcomes.pop(0)
        if isinstance(outcome, OpenError):
            raise outcome.exc
        process = FakeProcess(outcome)
        self.processes.append(process)
        return process

    async def start_sftp_client(self) -> FakeSftp:
        return self.sftp

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


@pytest.fixture
def store(tmp_path):
    return SshConfigStore(str(tmp_path / "connections.json"))


@pytest.fixture
async def seeded_store(store: SshConfigStore):
    await store.save({
        "name": "web", "host": "192.168.1.10", "port": 22,
        "username": "root", "password": "secret", "description": "生产",
    })
    return store


@pytest.fixture
def manager(seeded_store: SshConfigStore):
    return SshConnectionManager(seeded_store)


class TestResolveName:
    def test_empty_uses_default(self, manager: SshConnectionManager) -> None:
        assert manager.resolve_name("") == "web"

    def test_explicit_name(self, manager: SshConnectionManager) -> None:
        assert manager.resolve_name("web") == "web"

    def test_no_config_raises(self, store: SshConfigStore) -> None:
        empty_mgr = SshConnectionManager(store)
        with pytest.raises(ValueError):
            empty_mgr.resolve_name("")


class TestExecute:
    async def test_structured_result(self, manager: SshConnectionManager) -> None:
        fake = FakeConn([FakeResult(0, "hello\n", "")])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            result = await manager.execute("echo hi")
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert result["stdout"] == "hello\n"
        assert result["connection"] == "web"
        assert fake.run_commands == ["echo hi"]

    async def test_nonzero_exit_code(self, manager: SshConnectionManager) -> None:
        fake = FakeConn([FakeResult(127, "", "not found")])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            result = await manager.execute("badcmd")
        assert result["ok"] is False
        assert result["exit_code"] == 127
        assert result["stderr"] == "not found"

    async def test_work_dir_prefix(self, manager: SshConnectionManager) -> None:
        fake = FakeConn([FakeResult(0, "", "")])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            await manager.execute("ls", work_dir="/var/log")
        assert fake.run_commands == ["cd /var/log && ls"]

    async def test_connection_reused(self, manager: SshConnectionManager) -> None:
        """连续两次执行复用同一连接（connect 只调用一次）。"""
        fake = FakeConn([FakeResult(0, "a", ""), FakeResult(0, "b", "")])
        connect_mock = AsyncMock(return_value=fake)
        with patch("entities.ssh.manager.asyncssh.connect", connect_mock):
            await manager.execute("cmd1")
            await manager.execute("cmd2")
        assert connect_mock.call_count == 1
        assert len(fake.run_commands) == 2

    async def test_open_failure_reconnects_and_retries(self, manager: SshConnectionManager) -> None:
        """会话打开失败（命令未开始，如池内失效连接）→ 重连并完整重试一次。"""
        broken = FakeConn([OpenError(asyncssh.ConnectionLost("网络中断"))])
        healthy = FakeConn([FakeResult(0, "recovered", "")])
        with patch(
            "entities.ssh.manager.asyncssh.connect",
            AsyncMock(side_effect=[broken, healthy]),
        ):
            result = await manager.execute("cmd")
        assert result["ok"] is True
        assert result["stdout"] == "recovered"

    async def test_open_failure_twice_raises_connection_error(
        self, manager: SshConnectionManager,
    ) -> None:
        """重连后打开仍失败 → ConnectionError（归因 network），不再无限重试。"""
        broken = FakeConn([OpenError(asyncssh.ConnectionLost("网络中断"))])
        broken2 = FakeConn([OpenError(asyncssh.ConnectionLost("仍失败"))])
        with patch(
            "entities.ssh.manager.asyncssh.connect",
            AsyncMock(side_effect=[broken, broken2]),
        ):
            with pytest.raises(ConnectionError):
                await manager.execute("cmd")

    async def test_interrupted_mid_command_fails_fast(
        self, manager: SshConnectionManager,
    ) -> None:
        """命令执行中断开（对端关机等）→ 立即抛 SshCommandInterrupted，不重连。"""
        fake = FakeConn([WaitError(asyncssh.ConnectionLost("Connection reset by peer"))])
        connect_mock = AsyncMock(return_value=fake)
        with patch("entities.ssh.manager.asyncssh.connect", connect_mock):
            with pytest.raises(SshCommandInterrupted) as excinfo:
                await manager.execute("sleep 60")
        err = excinfo.value
        assert err.connection == "web"
        assert "reset" in err.reason
        assert err.elapsed_ms >= 0
        # 关键断言：未做任何重连尝试
        assert connect_mock.call_count == 1
        snapshot = manager.get_snapshot("web")
        assert snapshot is not None
        assert snapshot["status"] == STATUS_DISCONNECTED

    async def test_command_timeout_closes_process(self, manager: SshConnectionManager) -> None:
        """命令超时 → 关闭执行通道（防远端进程在池化连接上滞留）并上抛超时。"""
        fake = FakeConn([WaitError(asyncio.TimeoutError())])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            with pytest.raises(asyncio.TimeoutError):
                await manager.execute("sleep 999")
        assert fake.processes[0].closed is True


class TestConnectDisconnect:
    async def test_connect_sets_status(self, manager: SshConnectionManager) -> None:
        fake = FakeConn([])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            snapshot = await manager.connect("web")
        assert snapshot["status"] == STATUS_CONNECTED
        assert snapshot["is_default"] is True

    async def test_connect_missing_raises(self, manager: SshConnectionManager) -> None:
        with pytest.raises(ValueError):
            await manager.connect("ghost")

    async def test_disconnect_resets_status(self, manager: SshConnectionManager) -> None:
        fake = FakeConn([])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            await manager.connect("web")
        await manager.disconnect("web")
        snapshot = manager.get_snapshot("web")
        assert snapshot is not None
        assert snapshot["status"] == STATUS_DISCONNECTED
        assert fake.closed is True


class TestSftp:
    async def test_upload(self, manager: SshConnectionManager, tmp_path) -> None:
        local = tmp_path / "up.txt"
        local.write_text("data")
        fake = FakeConn([])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            result = await manager.upload(str(local), "/remote/up.txt")
        assert result["ok"] is True
        assert result["size"] == 4
        assert fake.sftp.put_calls == [(str(local), "/remote/up.txt")]
        assert fake.sftp.exited is True

    async def test_upload_missing_local_raises(self, manager: SshConnectionManager) -> None:
        with pytest.raises(FileNotFoundError):
            await manager.upload("/nonexistent/file.txt", "/remote/x")

    async def test_download(self, manager: SshConnectionManager, tmp_path) -> None:
        local = tmp_path / "down.txt"
        fake = FakeConn([])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            result = await manager.download("/remote/down.txt", str(local))
        assert result["ok"] is True
        assert fake.sftp.get_calls == [("/remote/down.txt", str(local))]


class TestStatusSnapshot:
    async def test_list_statuses_masks_nothing_but_state(self, manager: SshConnectionManager) -> None:
        statuses = manager.list_statuses()
        assert len(statuses) == 1
        entry = statuses[0]
        assert entry["name"] == "web"
        assert entry["status"] == STATUS_DISCONNECTED
        # 快照不含凭据字段
        assert "password" not in entry
        assert "key_path" not in entry

    async def test_remove_profile_clears_pool(self, manager: SshConnectionManager) -> None:
        fake = FakeConn([])
        with patch("entities.ssh.manager.asyncssh.connect", AsyncMock(return_value=fake)):
            await manager.connect("web")
        assert await manager.remove_profile("web") is True
        assert manager.get_snapshot("web") is None
        assert fake.closed is True
