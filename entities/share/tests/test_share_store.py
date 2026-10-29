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


async def test_media_visit_reuses_one_atomic_quota(store, monkeypatch):
    import asyncio
    import html
    import re
    import time

    import httpx
    from fastapi import FastAPI

    from entities.share import router

    s, ws = store
    (ws / "photo.png").write_bytes(b"0123456789")
    entry = await s.create(file_path="photo.png", share_type="media", max_downloads=1)
    token = entry["token"]
    monkeypatch.setattr(router, "get_share_store", lambda: s)
    app = FastAPI()
    app.include_router(router.build_router(), prefix="/api/entity/share")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*(client.get(f"/api/entity/share/v/{token}") for _ in range(4)))
        assert sorted(r.status_code for r in responses) == [200, 404, 404, 404]
        page = next(r for r in responses if r.status_code == 200)
        raw = html.unescape(re.search(r'(/api/entity/share/raw/[^"<> ]+)', page.text).group(1))
        assert (await client.get(raw)).content == b"0123456789"
        part = await client.get(raw, headers={"Range": "bytes=2-5"})
        assert part.status_code == 206 and part.content == b"2345"
        assert (await client.get(raw.replace("/raw/", "/d/"))).status_code == 200
        assert (await s.get_by_token(token))["download_count"] == 1
        assert (await s.get_download_logs(token=token))["total"] == 1
        assert (await s.list(status="active"))["total"] == 0
        assert (await s.list(status="expired"))["items"][0]["status"] == "expired"
        assert (await s.stats())["expired"] == 1
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as stranger:
            assert (await stranger.get(f"/api/entity/share/raw/{token}")).status_code == 404
            assert (await stranger.get(raw + "tampered")).status_code == 404
        with monkeypatch.context() as m:
            now = time.time()
            m.setattr("entities.share.store.time.time", lambda: now + 3601)
            assert (await client.get(raw)).status_code == 404
        await s.revoke(token)
        assert (await client.get(raw)).status_code == 404


async def test_direct_raw_claims_quota_and_reuses_ranges(store, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from entities.share import router

    s, ws = store
    (ws / "movie.mp4").write_bytes(b"0123456789")
    entry = await s.create(file_path="movie.mp4", share_type="media", max_downloads=1)
    monkeypatch.setattr(router, "get_share_store", lambda: s)
    app = FastAPI()
    app.include_router(router.build_router(), prefix="/api/entity/share")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/entity/share/raw/{entry['token']}"
        assert (await client.get(url)).status_code == 200
        assert (await client.get(url, headers={"Range": "bytes=0-2"})).status_code == 206
        assert (await s.get_by_token(entry["token"]))["download_count"] == 1
    renewed = await s.create(file_path="movie.mp4", share_type="media", max_downloads=1)
    assert renewed["token"] != entry["token"]
    assert not s.valid_access(renewed["token"], s.issue_access(entry["token"]))


@pytest.mark.parametrize("invalid", ["", "bad", "123.中文", "9" * 100 + "." + "a" * 64])
async def test_access_rejects_malformed_credentials(store, invalid):
    s, _ = store
    assert not s.valid_access("token", invalid)


@pytest.mark.parametrize("kind", ["file", "link"])
async def test_single_access_download_and_link_page(store, monkeypatch, kind):
    import httpx
    from fastapi import FastAPI

    from entities.share import router

    s, ws = store
    (ws / "note.txt").write_text("note")
    entry = await s.create(file_path="note.txt", share_type=kind,
                           target_url="https://example.com" if kind == "link" else "", max_downloads=1)
    monkeypatch.setattr(router, "get_share_store", lambda: s)
    app = FastAPI()
    app.include_router(router.build_router(), prefix="/api/entity/share")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=True) as client:
        url = f"/api/entity/share/v/{entry['token']}"
        assert (await client.get(url)).status_code == 200
        assert (await client.get(url)).status_code == 404
        assert (await s.get_by_token(entry["token"]))["download_count"] == 1


async def test_failed_audit_rolls_back_quota(store, monkeypatch):
    s, ws = store
    (ws / "one.txt").write_text("one")
    entry = await s.create(file_path="one.txt", max_downloads=1)
    db = await s._get_db()
    execute = db.execute
    def fail_audit(sql, *args, **kwargs):
        if sql.startswith("INSERT INTO download_logs"):
            raise OSError("audit storage failed")
        return execute(sql, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(db, "execute", fail_audit)
        with pytest.raises(OSError):
            await s.claim_access(entry["token"])
    assert (await s.get_by_token(entry["token"]))["download_count"] == 0
    assert await s.claim_access(entry["token"])
