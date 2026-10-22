"""core/instance_guard 单元测试：PID 文件 / 残留清场 / 防误杀 / 释放。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.instance_guard import acquire_instance, release_instance


@pytest.fixture
def pid_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "anelf.pid"
    yield path
    release_instance(path)


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
    import psutil
    return psutil.pid_exists(pid)


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
            with pytest.raises(RuntimeError, match="仍存活"):
                acquire_instance(pid_file, root)
            assert _alive(proc.pid)  # 未被动过
            assert pid_file.read_text() == str(proc.pid)
        finally:
            proc.kill()
            proc.wait()

    def test_live_instance_is_not_interrupted(self, pid_file, root):
        """重复启动不能终止仍可能在写数据库的旧进程。"""
        proc = _spawn_sleep()
        try:
            pid_file.write_text(str(proc.pid))
            with pytest.raises(RuntimeError, match="仍存活"):
                acquire_instance(pid_file, root)
            assert _alive(proc.pid)
        finally:
            proc.kill()
            proc.wait()

    def test_file_lock_blocks_second_process_even_without_pid_file(self, pid_file, root):
        acquire_instance(pid_file, root)
        pid_file.unlink()
        code = ("from pathlib import Path; from core.instance_guard import acquire_instance; "
                f"acquire_instance(Path({str(pid_file)!r}), Path({str(root)!r}))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
        assert result.returncode != 0
        assert not pid_file.exists()

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


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate is not a graceful POSIX signal")
@pytest.mark.parametrize("ignore_term", [False, True])
def test_stop_uses_sigterm_and_never_escalates(tmp_path, ignore_term):
    from core.instance_guard import stop_instance

    logs = tmp_path / "logs"
    logs.mkdir()
    script = tmp_path / "launch.py"
    script.write_text(
        "import os, signal, time\nfrom pathlib import Path\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
        + "Path('logs/anelf.pid').write_text(str(os.getpid()))\ntime.sleep(60)\n",
    )
    process = subprocess.Popen([sys.executable, "launch.py"], cwd=tmp_path)
    try:
        deadline = time.monotonic() + 5
        while not (logs / "anelf.pid").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        if ignore_term:
            with pytest.raises(RuntimeError, match="未强杀"):
                stop_instance(tmp_path, timeout=0.1)
            assert process.poll() is None
        else:
            stop_instance(tmp_path, timeout=5)
            assert process.wait(timeout=3) == -15
    finally:
        # 只清理本测试无数据库的睡眠子进程。
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
