"""core/instance_guard 单元测试：PID 文件 / 残留清场 / 防误杀 / 释放。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.instance_guard import acquire_instance, release_instance


@pytest.fixture
def pid_file(tmp_path: Path) -> Path:
    return tmp_path / "anelf.pid"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


def _spawn_sleep(ignore_term: bool = False) -> subprocess.Popen:
    code = "import time; time.sleep(60)"
    if ignore_term:
        code = ("import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)")
    return subprocess.Popen([sys.executable, "-c", code])


def _dead_pid() -> int:
    """拿一个确定已退出且未被复用的 PID。"""
    proc = _spawn_sleep()
    proc.terminate()
    proc.wait()
    return proc.pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class TestAcquire:
    def test_fresh_acquire_writes_pid(self, pid_file, root):
        assert acquire_instance(pid_file, root) is None
        assert pid_file.read_text() == str(os.getpid())

    def test_dead_stale_pid_overwritten(self, pid_file, root):
        pid_file.write_text(str(_dead_pid()))
        assert acquire_instance(pid_file, root) is None
        assert pid_file.read_text() == str(os.getpid())

    def test_foreign_process_never_killed(self, pid_file, root):
        """PID 复用/外来进程：cmdline 不属于本项目 launch.py，绝不终止。"""
        proc = _spawn_sleep()
        try:
            pid_file.write_text(str(proc.pid))
            assert acquire_instance(pid_file, root) is None
            assert _alive(proc.pid)  # 未被动过
        finally:
            proc.kill()
            proc.wait()

    def test_stale_instance_terminated(self, pid_file, root, monkeypatch):
        """残留实例（cmdline 校验为本项目）：SIGTERM 优雅终止后接管。"""
        monkeypatch.setattr("core.instance_guard._is_our_instance",
                            lambda pid, project_root: True)
        proc = _spawn_sleep()
        pid_file.write_text(str(proc.pid))
        killed = acquire_instance(pid_file, root)
        assert killed == proc.pid
        assert proc.wait(timeout=3) is not None  # 已退出（wait 同时收割僵尸）
        assert pid_file.read_text() == str(os.getpid())

    def test_sigterm_ignoring_instance_escalates_to_sigkill(
            self, pid_file, root, monkeypatch):
        """无视 SIGTERM 的顽固实例：宽限期后升级 SIGKILL。"""
        monkeypatch.setattr("core.instance_guard._is_our_instance",
                            lambda pid, project_root: True)
        monkeypatch.setattr("core.instance_guard._TERM_GRACE_SECONDS", 0.5)
        monkeypatch.setattr("core.instance_guard._TERM_POLL_INTERVAL", 0.05)
        proc = _spawn_sleep(ignore_term=True)
        pid_file.write_text(str(proc.pid))
        killed = acquire_instance(pid_file, root)
        assert killed == proc.pid
        assert proc.wait(timeout=3) is not None

    def test_corrupt_pid_file_recovered(self, pid_file, root):
        pid_file.write_text("not-a-pid")
        assert acquire_instance(pid_file, root) is None
        assert pid_file.read_text() == str(os.getpid())


class TestRelease:
    def test_release_removes_own_pid(self, pid_file, root):
        acquire_instance(pid_file, root)
        release_instance(pid_file)
        assert not pid_file.exists()

    def test_release_ignores_foreign_pid(self, pid_file):
        pid_file.write_text("123456")
        release_instance(pid_file)
        assert pid_file.exists()  # 指向别人，不动
