"""StickerStore 向量重建链路测试（clear / list_missing / set_embedding）。"""

from __future__ import annotations

import pytest

from entities.sticker.store import StickerStore


@pytest.fixture
async def store(tmp_path):
    s = StickerStore(str(tmp_path / "stickers.sqlite3"))
    yield s
    await s.close()


async def _seed(store: StickerStore) -> None:
    await store.add_sticker(
        file_path="/tmp/a.png", description="开心的猫", tags=["猫"],
        content_hash="hash_a", embedding=[0.1, 0.2, 0.3],
    )
    await store.add_sticker(
        file_path="/tmp/b.png", description="无语望天", tags=["无语"],
        content_hash="hash_b",
    )
    await store.upsert_image(path="/tmp/c.png", description="架构图")


class TestListMissingEmbedding:
    async def test_returns_rows_from_both_tables(self, store: StickerStore) -> None:
        await _seed(store)
        rows = await store.list_missing_embedding(10)
        kinds = {r["kind"] for r in rows}
        ids = {r["id"] for r in rows}
        # 有向量的贴纸不在结果中；无向量的贴纸 + 图片在
        assert kinds == {"stickers", "images"}
        assert "/tmp/c.png" in ids
        assert all(r["file_path"] != "/tmp/a.png" for r in rows)
        sticker_row = next(r for r in rows if r["kind"] == "stickers")
        assert sticker_row["tags"] == ["无语"]

    async def test_limit_split_across_tables(self, store: StickerStore) -> None:
        await _seed(store)
        rows = await store.list_missing_embedding(1)
        assert len(rows) == 1

    async def test_empty_when_all_embedded(self, store: StickerStore) -> None:
        await store.add_sticker(
            file_path="/tmp/a.png", description="x", tags=[],
            content_hash="h", embedding=[0.1, 0.2],
        )
        assert await store.list_missing_embedding(10) == []


class TestSetAndClearEmbedding:
    async def test_set_embedding_roundtrip(self, store: StickerStore) -> None:
        await _seed(store)
        rows = await store.list_missing_embedding(10)
        target = next(r for r in rows if r["kind"] == "stickers")
        await store.set_embedding(target["kind"], target["id"], [0.5, 0.6])
        remaining = await store.list_missing_embedding(10)
        assert all(r["id"] != target["id"] for r in remaining)
        sticker = await store.get_sticker(target["id"])
        assert sticker is not None and sticker["has_embedding"] is True

    async def test_clear_embeddings(self, store: StickerStore) -> None:
        await _seed(store)
        cleared = await store.clear_embeddings()
        assert cleared == 1  # 仅 sticker_a 原本有向量
        rows = await store.list_missing_embedding(10)
        assert len(rows) == 3
