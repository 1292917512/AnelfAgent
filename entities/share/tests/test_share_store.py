"""ShareStore 多类型分享测试（file / media / link + 建表幂等 + 查重）。"""

from __future__ import annotations

import os

import pytest

from core.config import ConfigManager
from entities.share.store import (
    ShareStore,
    _link_name_from_url,
    build_view_url,
    detect_media_kind,
)


@pytest.fixture
async def store(tmp_path):
    # 沙箱指向临时工作区，避免触碰真实 workspace
    ws = tmp_path / "ws"
    ws.mkdir()
    ConfigManager.set("workspace_root", str(ws))
    s = ShareStore(str(tmp_path / "share.sqlite3"))
    yield s, ws
    await s.close()


async def _make_file(ws, rel: str, content: bytes = b"hello") -> str:
    fp = ws / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(content)
    return rel


class TestCreateTypes:
    async def test_create_file_share(self, store) -> None:
        s, ws = store
        rel = await _make_file(ws, "uploads/report.pdf")
        entry = await s.create(file_path=rel, share_type="file")
        assert entry["share_type"] == "file"
        assert entry["media_kind"] == ""
        assert entry["file_name"] == "report.pdf"
        assert entry["file_size"] == 5
        assert entry["status"] == "active"

    async def test_create_media_share_detects_kind(self, store) -> None:
        s, ws = store
        rel = await _make_file(ws, "uploads/photo.png")
        entry = await s.create(file_path=rel, share_type="media")
        assert entry["share_type"] == "media"
        assert entry["media_kind"] == "image"

    async def test_media_share_rejects_unrenderable(self, store) -> None:
        s, ws = store
        rel = await _make_file(ws, "uploads/data.csv")
        with pytest.raises(ValueError, match="不可渲染"):
            await s.create(file_path=rel, share_type="media")

    async def test_create_link_share(self, store) -> None:
        s, _ = store
        entry = await s.create(
            target_url="http://127.0.0.1:8080/dashboard",
            share_type="link",
            description="本地面板",
        )
        assert entry["share_type"] == "link"
        assert entry["target_url"] == "http://127.0.0.1:8080/dashboard"
        assert "127.0.0.1:8080" in entry["file_name"]
        assert entry["file_size"] == 0

    async def test_link_share_requires_url(self, store) -> None:
        s, _ = store
        with pytest.raises(ValueError, match="target_url"):
            await s.create(share_type="link")

    async def test_link_share_rejects_bad_scheme(self, store) -> None:
        s, _ = store
        with pytest.raises(ValueError, match="http"):
            await s.create(target_url="ftp://example.com", share_type="link")

    async def test_unknown_type_rejected(self, store) -> None:
        s, _ = store
        with pytest.raises(ValueError, match="不支持的分享类型"):
            await s.create(share_type="bogus")


class TestDeduplication:
    async def test_file_dedup_same_content(self, store) -> None:
        s, ws = store
        rel = await _make_file(ws, "a.txt")
        first = await s.create(file_path=rel)
        second = await s.create(file_path=rel)
        assert first["token"] == second["token"]
        assert second.get("deduplicated") is True

    async def test_file_vs_media_not_deduped(self, store) -> None:
        s, ws = store
        rel = await _make_file(ws, "b.png")
        file_entry = await s.create(file_path=rel, share_type="file")
        media_entry = await s.create(file_path=rel, share_type="media")
        assert file_entry["token"] != media_entry["token"]

    async def test_link_dedup_same_url(self, store) -> None:
        s, _ = store
        first = await s.create(target_url="https://example.com", share_type="link")
        second = await s.create(target_url="https://example.com", share_type="link")
        assert first["token"] == second["token"]


class TestSchemaIdempotent:
    async def test_migration_idempotent(self, tmp_path) -> None:
        db_path = str(tmp_path / "idem.sqlite3")
        s1 = ShareStore(db_path)
        await s1.list(status="all")
        await s1.close()
        s2 = ShareStore(db_path)
        try:
            rows = await s2.list(status="all")
            assert rows["total"] == 0
        finally:
            await s2.close()


class TestHelpers:
    def test_detect_media_kind(self) -> None:
        assert detect_media_kind("a.png") == "image"
        assert detect_media_kind("a.MP4") == "video"
        assert detect_media_kind("a.mp3") == "audio"
        assert detect_media_kind("a.pdf") == "pdf"
        assert detect_media_kind("a.html") == "html"
        assert detect_media_kind("a.zip") == ""
        assert detect_media_kind("noext") == ""

    def test_link_name_from_url(self) -> None:
        assert _link_name_from_url("http://127.0.0.1:8080") == "127.0.0.1:8080"
        assert _link_name_from_url("https://a.cn/x/y") == "a.cn/x/y"
        assert _link_name_from_url("https://a.cn/") == "a.cn"

    def test_build_view_url(self) -> None:
        assert build_view_url("tok", "") == "/api/entity/share/v/tok"
        assert build_view_url("tok", "https://d.cn/") == "https://d.cn/api/entity/share/v/tok"

    async def test_fresh_schema_has_new_columns(self, tmp_path) -> None:
        s = ShareStore(str(tmp_path / "fresh.sqlite3"))
        try:
            db = await s._get_db()
            cursor = await db.execute("PRAGMA table_info(share_links)")
            cols = {r["name"] for r in await cursor.fetchall()}
            assert {"share_type", "target_url", "media_kind"} <= cols
        finally:
            await s.close()


class TestListAndStats:
    async def test_list_includes_new_fields(self, store) -> None:
        s, _ = store
        await s.create(target_url="https://example.com", share_type="link")
        result = await s.list(status="active")
        assert result["total"] == 1
        item = result["items"][0]
        assert item["share_type"] == "link"
        assert item["target_url"] == "https://example.com"

    async def test_stats_counts_all_types(self, store) -> None:
        s, ws = store
        rel = await _make_file(ws, "c.png")
        await s.create(file_path=rel, share_type="media")
        await s.create(target_url="https://example.com", share_type="link")
        stats = await s.stats()
        assert stats["total"] == 2
        assert stats["active"] == 2

    async def test_sandbox_rejects_outside_path(self, store) -> None:
        s, ws = store
        outside = os.path.join(os.path.dirname(str(ws)), "outside.txt")
        with open(outside, "w") as f:
            f.write("x")
        with pytest.raises(ValueError, match="沙箱"):
            await s.create(file_path=outside)
