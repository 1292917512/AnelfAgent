"""文件读取基线：覆盖累计、内容冲突、scope 隔离与缓存配额。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from entities.filesystem import file_state
from entities.filesystem.file_state import FileState, FileStateCache


@pytest.fixture()
def cache_scope(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(file_state, "get_current_scope", lambda: "_test")
    file_state.clear_scope("_test")
    yield
    file_state.clear_scope("_test")


class TestFileStateCache:
    def test_lru_eviction_by_entries(self) -> None:
        cache = FileStateCache(max_entries=3)
        for i in range(3):
            cache.set(f"/f{i}", FileState("x", ((0, 1),)))
        cache.get("/f0")
        cache.set("/f3", FileState("x", ((0, 1),)))
        assert cache.get("/f1") is None
        assert cache.get("/f0") is not None

    def test_lru_eviction_counts_utf8_bytes(self) -> None:
        cache = FileStateCache(max_entries=100, max_bytes=10)
        cache.set("/a", FileState("中文", ((0, 2),)))
        cache.set("/b", FileState("文本", ((0, 2),)))
        assert cache.get("/a") is None
        assert cache.get("/b") is not None
        cache.set("/b", FileState("x", ((0, 1),)))
        cache.set("/c", FileState("中文", ((0, 2),)))
        assert cache.get("/b") is not None

    def test_path_normalization(self) -> None:
        cache = FileStateCache()
        cache.set("/a/./b", FileState("x", ((0, 1),)))
        assert cache.get("/a/b") is not None

    def test_concurrent_pages_keep_all_coverage(self) -> None:
        cache = FileStateCache()
        content = "x" * 100
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda i: cache.record_read("/a", content, ((i, i + 1),)), range(100)))
        state = cache.get("/a")
        assert state is not None and state.is_complete

    def test_scope_isolation(self) -> None:
        try:
            file_state.record_read("/a", "x", read_ranges=((0, 1),), scope="reader")
            assert file_state.check_writable("/a", "x", scope="reader")[0]
            assert not file_state.check_writable("/a", "x", scope="other")[0]
        finally:
            file_state.clear_scope("reader")
            file_state.clear_scope("other")


@pytest.mark.usefixtures("cache_scope")
class TestCheckWritable:
    def test_missing_partial_and_complete_reads(self) -> None:
        assert "尚未读取" in file_state.check_writable("/a", "a\nb\nc")[1]
        file_state.record_read("/a", "a\nb\nc", read_ranges=((4, 5),))
        assert "offset=1" in file_state.check_writable("/a", "a\nb\nc")[1]
        file_state.record_read("/a", "a\nb\nc", read_ranges=((0, 2),))
        assert "offset=3" in file_state.check_writable("/a", "a\nb\nc")[1]
        file_state.record_read("/a", "a\nb\nc", read_ranges=((2, 5),))
        assert file_state.check_writable("/a", "a\nb\nc")[0]
        file_state.record_read("/a", "a\nb\nc", read_ranges=((2, 4),))
        assert file_state.check_writable("/a", "a\nb\nc")[0]

    def test_changed_content_restarts_coverage(self) -> None:
        file_state.record_read("/a", "a\nb", read_ranges=((0, 3),))
        assert "已被修改" in file_state.check_writable("/a", "a\nc")[1]
        file_state.record_read("/a", "a\nc", read_ranges=((2, 3),))
        assert "offset=1" in file_state.check_writable("/a", "a\nc")[1]
        file_state.record_read("/a", "a\nc", read_ranges=((0, 2),))
        assert file_state.check_writable("/a", "a\nc")[0]

    def test_write_refreshes_normalized_baseline(self) -> None:
        file_state.record_write("/a", "a\r\nb\r\n")
        assert file_state.check_writable("/a", "a\nb\n")[0]

    def test_invalid_or_unseen_range_cannot_authorize_write(self) -> None:
        file_state.record_read("/a", "x", read_ranges=())
        assert not file_state.check_writable("/a", "x")[0]
        with pytest.raises(ValueError):
            file_state.record_read("/a", "x", read_ranges=((0, 2),))
        assert not file_state.check_writable("/a", "x")[0]
