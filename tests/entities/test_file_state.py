"""file_state 文件读取状态缓存测试（移植自 Claude Code fileStateCache 语义）。"""

from __future__ import annotations

import os
import time

import pytest

from entities.filesystem import file_state
from entities.filesystem.file_state import FileStateCache


@pytest.fixture()
def cache_scope(tmp_path, monkeypatch):
    """隔离的 scope，避免污染全局缓存。"""
    monkeypatch.setattr(file_state, "get_current_scope", lambda: "_test")
    file_state.clear_scope("_test")
    yield
    file_state.clear_scope("_test")


class TestFileStateCache:
    def test_lru_eviction_by_entries(self):
        cache = FileStateCache(max_entries=3)
        for i in range(4):
            cache.set(f"/f{i}", file_state.FileState("x", 1.0, 1.0))
        assert cache.get("/f0") is None
        assert cache.get("/f3") is not None

    def test_lru_eviction_by_bytes(self):
        cache = FileStateCache(max_entries=100, max_bytes=10)
        cache.set("/a", file_state.FileState("12345", 1.0, 1.0))
        cache.set("/b", file_state.FileState("67890", 1.0, 1.0))
        cache.set("/c", file_state.FileState("abc", 1.0, 1.0))
        assert cache.get("/a") is None
        assert cache.get("/c") is not None

    def test_path_normalization(self):
        cache = FileStateCache()
        cache.set("/a/./b", file_state.FileState("x", 1.0, 1.0))
        assert cache.get("/a/b") is not None


class TestCheckWritable:
    def test_not_read_rejected(self, cache_scope, tmp_path):
        fp = tmp_path / "a.txt"
        fp.write_text("hello")
        ok, message = file_state.check_writable(str(fp))
        assert not ok
        assert "尚未读取" in message

    def test_partial_read_rejected(self, cache_scope, tmp_path):
        fp = tmp_path / "a.txt"
        fp.write_text("hello")
        file_state.record_read(str(fp), "hello", os.path.getmtime(fp), offset=1, limit=5)
        ok, message = file_state.check_writable(str(fp))
        assert not ok
        assert "部分读取" in message

    def test_full_read_allowed(self, cache_scope, tmp_path):
        fp = tmp_path / "a.txt"
        fp.write_text("hello")
        file_state.record_read(str(fp), "hello", os.path.getmtime(fp))
        ok, _ = file_state.check_writable(str(fp))
        assert ok

    def test_stale_content_rejected(self, cache_scope, tmp_path):
        fp = tmp_path / "a.txt"
        fp.write_text("hello")
        file_state.record_read(str(fp), "hello", os.path.getmtime(fp))
        # 外部修改：mtime 变晚且内容不同
        time.sleep(0.01)
        fp.write_text("changed by someone else")
        os.utime(fp, (time.time() + 1, time.time() + 1))
        ok, message = file_state.check_writable(str(fp))
        assert not ok
        assert "已被修改" in message

    def test_mtime_bump_same_content_allowed(self, cache_scope, tmp_path):
        fp = tmp_path / "a.txt"
        fp.write_text("hello")
        file_state.record_read(str(fp), "hello", os.path.getmtime(fp))
        # 仅触碰 mtime（云同步/杀软场景），内容不变 → 放行
        os.utime(fp, (time.time() + 1, time.time() + 1))
        ok, _ = file_state.check_writable(str(fp))
        assert ok

    def test_crlf_content_compare_normalized(self, cache_scope, tmp_path):
        fp = tmp_path / "a.txt"
        fp.write_bytes(b"line1\r\nline2\r\n")
        file_state.record_read(str(fp), "line1\nline2\n", os.path.getmtime(fp))
        os.utime(fp, (time.time() + 1, time.time() + 1))
        ok, _ = file_state.check_writable(str(fp))
        assert ok

    def test_deleted_after_read_allowed(self, cache_scope, tmp_path):
        fp = tmp_path / "a.txt"
        fp.write_text("hello")
        file_state.record_read(str(fp), "hello", os.path.getmtime(fp))
        fp.unlink()
        ok, _ = file_state.check_writable(str(fp))
        assert ok
