"""后台子进程看护（child_guard）单元测试：登记 / 清扫 / 关停终止。"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from entities.filesystem import child_guard


@pytest.fixture
def registry_file(tmp_path, monkeypatch):
    monkeypatch.setattr(child_guard, "_registry_path",
                        lambda: tmp_path / "shell_children.json")
    return tmp_path / "shell_children.json"


def _spawn_sleeper() -> subprocess.Popen:
    """起一个长睡子进程（独立进程组，模拟后台 shell）。"""
    kwargs: dict = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
    )


class TestRegisterUnregister:
    def test_roundtrip(self, registry_file):
        child_guard.register_child(424242, "测试任务")
        data = child_guard._load()
        assert "424242" in data
        child_guard.unregister_child(424242)
        assert "424242" not in child_guard._load()

    def test_unregister_missing_is_noop(self, registry_file):
        child_guard.unregister_child(999999)


class TestSweep:
    def test_stale_group_swept(self, registry_file):
        """上次实例遗留的活进程组被终止并清账。"""
        proc = _spawn_sleeper()
        try:
            child_guard.register_child(proc.pid, "孤儿任务")
            # 伪装成上次实例登记（boot_id 不同）
            data = child_guard._load()
            data[str(proc.pid)]["boot_id"] = -1
            child_guard._save(data)

            swept = child_guard.sweep_stale_children()
            assert swept == 1
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.1)
            assert proc.poll() is not None
            assert str(proc.pid) not in child_guard._load()
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_dead_entry_cleaned_without_kill(self, registry_file):
        """登记里已死的组只清账，不算清扫数。"""
        child_guard.register_child(999999, "幽灵")
        assert child_guard.sweep_stale_children() == 0
        assert child_guard._load() == {}

    def test_current_boot_entry_kept(self, registry_file):
        """本次启动登记的活进程组不干预（等待线程在管）。"""
        proc = _spawn_sleeper()
        try:
            child_guard.register_child(proc.pid, "本次实例的")
            assert child_guard.sweep_stale_children() == 0
            assert str(proc.pid) in child_guard._load()
            assert proc.poll() is None  # 进程未被清扫波及
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class TestTerminateAll:
    def test_shutdown_terminates_live_children(self, registry_file):
        """关停清扫终止在册子进程并清空登记。"""
        proc = _spawn_sleeper()
        try:
            child_guard.register_child(proc.pid, "关停对象")
            killed = child_guard.terminate_all_children()
            assert killed == 1
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.1)
            assert proc.poll() is not None
            assert child_guard._load() == {}
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_no_children_zero(self, registry_file):
        assert child_guard.terminate_all_children() == 0
