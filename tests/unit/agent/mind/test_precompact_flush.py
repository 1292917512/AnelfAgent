"""PreCompact flush（压缩前记忆抢跑提取）测试：fail-open 与 scope 映射。"""

from __future__ import annotations

import asyncio

import pytest

from agent.memory import auto_capture, metrics
from agent.mind.tools.round_helpers import _precompact_flush


class _MindStub:
    def __init__(self, with_store: bool = True):
        self.memory_store = object() if with_store else None


@pytest.fixture(autouse=True)
def _reset_pipeline():
    auto_capture._pipeline = None
    yield
    auto_capture._pipeline = None


class TestScopeMapping:
    @pytest.mark.asyncio
    async def test_empty_scope_skipped(self):
        assert await _precompact_flush(_MindStub(), "") is False

    @pytest.mark.asyncio
    async def test_non_entity_scope_skipped(self):
        # reflect 等内部会话的 scope 不映射到会话存储，直接跳过
        assert await _precompact_flush(_MindStub(), "reflect_task") is False

    @pytest.mark.asyncio
    async def test_entity_scope_converted_to_capture_key(self, monkeypatch):
        seen: dict = {}

        async def fake_flush(mind, scope_key):
            seen["scope_key"] = scope_key
            return True

        monkeypatch.setattr(auto_capture, "flush_scope_capture", fake_flush)
        assert await _precompact_flush(_MindStub(), "group_qq:110") is True
        assert seen["scope_key"] == "group:qq:110"


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_flush_exception_never_propagates(self, monkeypatch):
        async def boom(mind, scope_key):
            raise RuntimeError("extract failed")

        monkeypatch.setattr(auto_capture, "flush_scope_capture", boom)
        before = metrics.snapshot().get("capture.precompact_flush_failed", 0)
        assert await _precompact_flush(_MindStub(), "user_qq:123") is False
        assert metrics.snapshot().get("capture.precompact_flush_failed", 0) == before + 1

    @pytest.mark.asyncio
    async def test_flush_timeout_returns_false(self, monkeypatch):
        async def slow(mind, scope_key):
            await asyncio.sleep(5)
            return True

        monkeypatch.setattr(auto_capture, "flush_scope_capture", slow)
        # 超时配置下限 1s：慢提取被截断，不阻断压缩
        monkeypatch.setattr("core.config.get_config_float", lambda k, d=0.0: 1.0)
        before = metrics.snapshot().get("capture.precompact_flush_timeout", 0)
        assert await _precompact_flush(_MindStub(), "user_qq:123") is False
        assert metrics.snapshot().get("capture.precompact_flush_timeout", 0) == before + 1

    @pytest.mark.asyncio
    async def test_config_disabled_skips(self, monkeypatch):
        called = False

        async def fake_flush(mind, scope_key):
            nonlocal called
            called = True
            return True

        monkeypatch.setattr(auto_capture, "flush_scope_capture", fake_flush)
        monkeypatch.setattr("core.config.get_config_bool", lambda k, d=False: False)
        assert await _precompact_flush(_MindStub(), "user_qq:123") is False
        assert not called


class TestFlushScopeCapture:
    @pytest.mark.asyncio
    async def test_bad_scope_key(self):
        assert await auto_capture.flush_scope_capture(_MindStub(), "nosplit") is False
        assert await auto_capture.flush_scope_capture(_MindStub(), "weird:qq:1") is False

    @pytest.mark.asyncio
    async def test_no_memory_store(self):
        assert await auto_capture.flush_scope_capture(_MindStub(with_store=False), "user:qq:1") is False

    @pytest.mark.asyncio
    async def test_forces_idle_zero_and_delegates(self, monkeypatch):
        """有待提取内容即到期（idle_seconds=0），委托给管线的单 scope 处理。"""
        calls: dict = {}

        class _FakePipeline:
            async def _process_scope(self, sqlite, scope_type, scope_id, *, every_n, idle_seconds, now):
                calls.update(scope_type=scope_type, scope_id=scope_id, idle_seconds=idle_seconds)
                return True

        class _FakeRT:
            class data_center:
                sqlite = object()

        monkeypatch.setattr(auto_capture, "_pipeline", _FakePipeline())
        monkeypatch.setattr("services._runtime.require_runtime", lambda: _FakeRT())
        assert await auto_capture.flush_scope_capture(_MindStub(), "user:qq:123") is True
        assert calls["scope_type"] == "user"
        assert calls["scope_id"] == "qq:123"
        assert calls["idle_seconds"] == 0
