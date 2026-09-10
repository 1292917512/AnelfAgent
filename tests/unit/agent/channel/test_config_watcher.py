"""agent/channel/config_watcher.py 目录结构监听（watch_dir）单元测试。

锁定：结构快照口径（子目录 + 标记文件存在性）与变化防抖（批量变化合并为一次回调）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.channel import config_watcher
from agent.channel.config_watcher import ConfigWatcher
from core.lifecycle import Lifecycle


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch):
    """加速轮询与防抖，避免测试等待真实秒级间隔。"""
    monkeypatch.setattr(config_watcher, "POLL_INTERVAL_SECONDS", 0.02)
    Lifecycle.reset()
    yield
    Lifecycle.reset()


class TestDirSnapshot:
    def test_snapshot_marks_marker_files(self, tmp_path: Path) -> None:
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "adapter.py").touch()
        (tmp_path / "beta").mkdir()  # 无标记文件
        (tmp_path / "_private").mkdir()
        (tmp_path / "_private" / "adapter.py").touch()  # 下划线路径跳过
        (tmp_path / "loose.py").touch()

        snapshot = ConfigWatcher._dir_snapshot(str(tmp_path), "adapter.py")
        assert snapshot == {"alpha": True, "beta": False}

    def test_snapshot_without_marker_lists_subdirs(self, tmp_path: Path) -> None:
        (tmp_path / "alpha").mkdir()
        assert ConfigWatcher._dir_snapshot(str(tmp_path), "") == {"alpha": True}


class TestWatchDir:
    async def test_batch_changes_debounced_to_single_callback(self, tmp_path: Path) -> None:
        watcher = ConfigWatcher()
        fired: list[int] = []
        watcher.watch_dir(str(tmp_path), lambda: fired.append(1), marker="adapter.py")
        try:
            await asyncio.sleep(0.05)  # 建立基线快照
            # 连续批量变化（模拟放进一个目录的多次文件操作）
            (tmp_path / "c1").mkdir()
            await asyncio.sleep(0.03)
            (tmp_path / "c1" / "adapter.py").touch()
            await asyncio.sleep(0.2)
            assert fired == [1]

            # 标记文件消失同样触发（目录拆除中途态）
            (tmp_path / "c1" / "adapter.py").unlink()
            await asyncio.sleep(0.2)
            assert fired == [1, 1]
        finally:
            watcher.stop_all()

    async def test_unwatch_dir_stops_callbacks(self, tmp_path: Path) -> None:
        watcher = ConfigWatcher()
        fired: list[int] = []
        watcher.watch_dir(str(tmp_path), lambda: fired.append(1))
        await asyncio.sleep(0.05)
        watcher.unwatch_dir(str(tmp_path))
        (tmp_path / "c1").mkdir()
        await asyncio.sleep(0.15)
        assert fired == []
        watcher.stop_all()
