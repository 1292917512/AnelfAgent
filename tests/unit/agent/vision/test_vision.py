"""视觉核心层测试：源注册表 / 帧缓冲判变 / 监视循环 / AI 工具 / 上下文注入。"""

from __future__ import annotations

import json
import time

import pytest

import agent.vision.buffer as buffer_mod
import agent.vision.framework as framework_mod
from agent.vision import (
    CapturedFrame,
    VisualSource,
    get_vision_buffer,
    get_vision_watcher,
)
from agent.vision.capture import grid_change_ratio, hamming


class FakeSource(VisualSource):
    key = "fake"
    display_name = "假源"
    description = "测试用轮询源"
    poll_interval = 1.0
    can_capture = True

    def __init__(self) -> None:
        self.frames = 0

    async def capture(self):
        self.frames += 1
        return CapturedFrame(
            path=f"/tmp/fake_{self.frames}.jpg", width=100, height=80,
            captured_at=time.time())


class PushOnlySource(VisualSource):
    key = "push_only"
    display_name = "推送源"
    poll_interval = 0.0
    can_capture = False


@pytest.fixture(autouse=True)
def clean_registry():
    """视觉源注册表与缓冲/监视单例隔离。"""
    saved = dict(framework_mod._SOURCES)
    framework_mod._SOURCES.clear()
    get_vision_buffer().reset()
    yield
    framework_mod._SOURCES.clear()
    framework_mod._SOURCES.update(saved)
    get_vision_buffer().reset()
    from core.config import ConfigManager
    ConfigManager.set("vision_disabled_sources", "")


@pytest.fixture
def fake_hash(monkeypatch: pytest.MonkeyPatch):
    """哈希打桩：按文件名尾号产出可控网格（判变路径确定性测试）。"""
    def _fake(path: str, grid: int = 4) -> list[int]:
        try:
            n = int(path.rsplit("_", 1)[1].split(".")[0])
        except (IndexError, ValueError):
            n = 0
        # 奇偶交替全 0/全 1：帧号变化即产生远超阈值的内容级变化
        return [0xFFFFFFFFFFFFFFFF if n % 2 else 0] * (grid * grid)
    monkeypatch.setattr(buffer_mod, "grid_dhash", _fake)
    return _fake


class TestFramework:
    def test_register_and_lookup(self) -> None:
        src = FakeSource()
        framework_mod.register_source(src)
        assert framework_mod.get_source("fake") is src
        assert src in framework_mod.all_sources()
        assert src in framework_mod.pollable_sources()

    def test_push_only_not_pollable(self) -> None:
        framework_mod.register_source(PushOnlySource())
        assert framework_mod.pollable_sources() == []

    def test_register_same_key_overwrites(self) -> None:
        a, b = FakeSource(), FakeSource()
        framework_mod.register_source(a)
        framework_mod.register_source(b)
        assert framework_mod.get_source("fake") is b

    def test_missing_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            framework_mod.register_source(VisualSource())


class TestBuffer:
    async def test_first_frame_is_change(self, fake_hash) -> None:
        _frame, changed = await get_vision_buffer().ingest("/tmp/fake_1.jpg", "fake")
        assert changed is True
        assert get_vision_buffer().latest is not None

    async def test_identical_frame_not_change(self, fake_hash) -> None:
        buffer = get_vision_buffer()
        await buffer.ingest("/tmp/fake_1.jpg", "fake")
        _f, changed = await buffer.ingest("/tmp/fake_1.jpg", "fake")
        assert changed is False

    async def test_changed_cells_update_latest(self, fake_hash) -> None:
        buffer = get_vision_buffer()
        await buffer.ingest("/tmp/fake_1.jpg", "fake")
        _f, changed = await buffer.ingest("/tmp/fake_2.jpg", "fake")
        assert changed is True
        assert buffer.latest is not None and buffer.latest.path.endswith("_2.jpg")

    async def test_sources_tracked_independently(self, fake_hash) -> None:
        buffer = get_vision_buffer()
        await buffer.ingest("/tmp/fake_1.jpg", "a")
        _f, changed = await buffer.ingest("/tmp/fake_1.jpg", "b")
        assert changed is True  # b 源首帧恒为变化
        assert set(buffer.latest_by_source) == {"a", "b"}


class TestHashMath:
    def test_hamming(self) -> None:
        assert hamming(0b1010, 0b0110) == 2

    def test_ratio_mismatch_is_full_change(self) -> None:
        assert grid_change_ratio([1], [1, 2], cell_threshold=3) == 1.0

    def test_ratio_counts_changed_cells(self) -> None:
        old = [0, 0, 0, 0]
        new = [0b1111, 0, 0, 0]
        assert grid_change_ratio(old, new, cell_threshold=3) == 0.25


class TestWatcher:
    async def test_start_status_stop(self, fake_hash) -> None:
        framework_mod.register_source(FakeSource())
        watcher = get_vision_watcher()
        assert await watcher.start("fake") is None
        assert watcher.watching("fake")
        assert "fake" in watcher.watching_sources()
        await watcher.stop("fake")
        assert not watcher.watching("fake")

    async def test_start_unknown_source_errors(self) -> None:
        watcher = get_vision_watcher()
        error = await watcher.start("不存在")
        assert error and "不存在" in error

    async def test_push_only_source_rejected(self) -> None:
        framework_mod.register_source(PushOnlySource())
        watcher = get_vision_watcher()
        error = await watcher.start("push_only")
        assert error and "不支持轮询" in error


class TestTools:
    async def test_vision_look_captures_frame(self, fake_hash) -> None:
        import agent.vision.tools as tools_mod
        framework_mod.register_source(FakeSource())
        raw = await tools_mod.vision_look("fake")
        body = json.loads(raw)
        assert body["_multimodal"] is True
        assert body["images"][0].endswith("_1.jpg")

    async def test_vision_look_unknown_source(self) -> None:
        import agent.vision.tools as tools_mod
        raw = await tools_mod.vision_look("不存在")
        body = json.loads(raw)
        assert body["cause"] == "not_found"

    async def test_vision_look_push_only_reads_buffer(self, fake_hash) -> None:
        import agent.vision.tools as tools_mod
        framework_mod.register_source(PushOnlySource())
        await get_vision_buffer().ingest("/tmp/fake_9.jpg", "push_only")
        raw = await tools_mod.vision_look("push_only")
        body = json.loads(raw)
        assert body["path"].endswith("_9.jpg")

    async def test_vision_watch_and_sources(self, fake_hash) -> None:
        import agent.vision.tools as tools_mod
        framework_mod.register_source(FakeSource())
        raw = await tools_mod.vision_watch("start", "fake")
        assert json.loads(raw)["success"] is True
        raw = tools_mod.vision_sources()
        body = json.loads(raw)
        entry = next(s for s in body["sources"] if s["key"] == "fake")
        assert entry["watching"] is True
        raw = await tools_mod.vision_watch("stop", "fake")
        assert json.loads(raw)["success"] is True


class TestContextProvider:
    async def test_no_active_source_no_injection(self) -> None:
        from agent.vision.context import VisionProvider
        provider = VisionProvider()
        assert await provider.provide("webui") is None

    async def test_injects_status_and_media_once(self, fake_hash) -> None:
        from agent.vision.context import VisionProvider
        framework_mod.register_source(FakeSource())
        buffer = get_vision_buffer()
        await buffer.ingest("/tmp/fake_1.jpg", "fake", width=100, height=80)
        provider = VisionProvider()
        snap = await provider.provide("webui")
        assert snap is not None and "fake" in snap.content
        assert len(snap.media) == 1  # 首帧携带画面
        status = provider.injection_status()
        assert status["last_media_inject"]["source"] == "fake"
        # 画面未变 → 后续只注入状态行不重复烧图片
        snap2 = await provider.provide("webui")
        assert snap2 is not None and not snap2.media


class TestWatcherUnregisteredExit:
    async def test_loop_exits_when_source_unregistered(self, fake_hash) -> None:
        """源被注销后监视循环自行退出（不围孤儿源空转）。"""
        src = FakeSource()
        framework_mod.register_source(src)
        watcher = get_vision_watcher()
        assert await watcher.start("fake") is None
        framework_mod.unregister_source("fake")
        task = watcher._tasks.get("fake")
        assert task is not None
        import asyncio
        await asyncio.wait_for(task, timeout=2.0)  # 循环到点自检退出
        assert task.done()


class TestSourceActivation:
    def test_disable_excludes_from_enabled(self) -> None:
        framework_mod.register_source(FakeSource())
        framework_mod.set_enabled("fake", False)
        assert not framework_mod.is_enabled("fake")
        assert framework_mod.enabled_sources() == []
        framework_mod.set_enabled("fake", True)
        assert framework_mod.is_enabled("fake")
        assert len(framework_mod.enabled_sources()) == 1

    async def test_watcher_refuses_disabled_source(self) -> None:
        framework_mod.register_source(FakeSource())
        framework_mod.set_enabled("fake", False)
        error = await get_vision_watcher().start("fake")
        assert error and "已停用" in error

    async def test_look_refuses_disabled_source(self) -> None:
        import agent.vision.tools as tools_mod
        framework_mod.register_source(FakeSource())
        framework_mod.set_enabled("fake", False)
        body = json.loads(await tools_mod.vision_look("fake"))
        assert "error" in body and "已停用" in body["error"]

    async def test_source_set_tool_roundtrip(self) -> None:
        import agent.vision.tools as tools_mod
        framework_mod.register_source(FakeSource())
        body = json.loads(tools_mod.vision_source_set("fake", False))
        assert body["enabled"] is False
        assert "fake" in body["disabled_sources"]
        body = json.loads(tools_mod.vision_source_set("fake", True))
        assert body["enabled"] is True
        assert "fake" not in body["disabled_sources"]

    async def test_disabled_source_excluded_from_injection(self, fake_hash) -> None:
        from agent.vision.context import VisionProvider
        framework_mod.register_source(FakeSource())
        await get_vision_buffer().ingest("/tmp/fake_1.jpg", "fake")
        framework_mod.set_enabled("fake", False)
        provider = VisionProvider()
        assert await provider.provide("webui") is None
