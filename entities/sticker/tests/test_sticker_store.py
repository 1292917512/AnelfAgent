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


class TestEmbeddingStats:
    async def test_stats_reports_dims_health(self, store: StickerStore) -> None:
        await _seed(store)
        stats = await store.stats()
        embedding = stats["embedding"]
        assert embedding["stored_dims"]["stickers"] == {"3": 1}
        assert embedding["stored_dims"]["images"] == {}
        assert embedding["missing"] == {"stickers": 1, "images": 1}
        if store._vec_available:
            assert embedding["vec_dims"]["stickers"] == 3
        else:
            assert embedding["vec_dims"]["stickers"] is None


class TestClearMismatchedEmbeddings:
    async def test_clears_only_mismatched_rows(self, store: StickerStore) -> None:
        await store.add_sticker(
            file_path="/tmp/a.png", description="三维", tags=[],
            content_hash="hash_a", embedding=[0.1, 0.2, 0.3],
        )
        await store.add_sticker(
            file_path="/tmp/b.png", description="五维", tags=[],
            content_hash="hash_b", embedding=[0.1] * 5,
        )
        cleared = await store.clear_mismatched_embeddings(3)
        assert cleared == {"stickers": 1, "images": 0}
        stats = await store.stats()
        # 仅 3 维向量保留，5 维向量被置空待回填
        assert stats["embedding"]["stored_dims"]["stickers"] == {"3": 1}
        assert stats["embedding"]["missing"]["stickers"] == 1

    async def test_drops_vec_index_when_meta_dims_differ(self, store: StickerStore) -> None:
        await store.add_sticker(
            file_path="/tmp/a.png", description="三维", tags=[],
            content_hash="hash_a", embedding=[0.1, 0.2, 0.3],
        )
        await store.clear_mismatched_embeddings(5)
        stats = await store.stats()
        # 索引维度（3）与目标维度（5）不一致：vec 索引随 meta 一并废弃
        assert stats["embedding"]["vec_dims"]["stickers"] is None
        if store._vec_available:
            db = await store._get_db()
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stickers_vec'")
            assert await cursor.fetchone() is None

    async def test_keeps_vec_index_when_dims_match(self, store: StickerStore) -> None:
        await store.add_sticker(
            file_path="/tmp/a.png", description="三维", tags=[],
            content_hash="hash_a", embedding=[0.1, 0.2, 0.3],
        )
        cleared = await store.clear_mismatched_embeddings(3)
        assert cleared == {"stickers": 0, "images": 0}
        if store._vec_available:
            stats = await store.stats()
            assert stats["embedding"]["vec_dims"]["stickers"] == 3
