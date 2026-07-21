"""文档解析与 uploads 索引链路测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.memory.doc_extract import extract_document_text
from agent.memory.embedder import Embedder
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_sync import sync_files
from agent.memory.memory_utils import list_indexable_files


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    yield s
    await s.close()


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "config"
    (ws / "memory").mkdir(parents=True)
    (ws / "memory" / "note.md").write_text("# 便签\n这是记忆文件内容", encoding="utf-8")
    return ws


@pytest.fixture
def uploads(tmp_path) -> Path:
    up = tmp_path / "uploads"
    docs = up / "docs"
    docs.mkdir(parents=True)
    (docs / "doc1.txt").write_text("上传文档一的内容，包含若干文字。", encoding="utf-8")
    return up


def _make_docx(path: Path, text: str) -> None:
    import docx
    document = docx.Document()
    document.add_paragraph(text)
    document.save(str(path))


class TestExtractDocumentText:
    def test_txt_and_md(self, tmp_path) -> None:
        p = tmp_path / "a.txt"
        p.write_text("纯文本内容", encoding="utf-8")
        assert extract_document_text(p) == "纯文本内容"

    def test_docx(self, tmp_path) -> None:
        p = tmp_path / "a.docx"
        _make_docx(p, "Word 文档段落内容")
        assert "Word 文档段落内容" in extract_document_text(p)

    def test_unsupported_ext(self, tmp_path) -> None:
        p = tmp_path / "a.xlsx"
        p.write_bytes(b"fake")
        with pytest.raises(ValueError, match="不支持"):
            extract_document_text(p)

    def test_invalid_pdf_raises(self, tmp_path) -> None:
        p = tmp_path / "broken.pdf"
        p.write_bytes(b"not a real pdf")
        with pytest.raises(Exception):
            extract_document_text(p)


class TestListIndexableFiles:
    def test_namespaces(self, workspace: Path, uploads: Path) -> None:
        pairs = dict((rel, p) for p, rel in list_indexable_files(workspace, uploads))
        assert "memory/note.md" in pairs
        assert "uploads/docs/doc1.txt" in pairs

    def test_no_uploads_dir(self, workspace: Path) -> None:
        pairs = list_indexable_files(workspace, None)
        assert [rel for _, rel in pairs] == ["memory/note.md"]


async def _chunk_texts(store: MemoryStore, path: str) -> list[str]:
    db = await store._get_db()
    cursor = await db.execute("SELECT text FROM chunks WHERE path=?", (path,))
    return [row["text"] for row in await cursor.fetchall()]


class TestSyncFilesWithDocs:
    async def test_docs_indexed(self, store: MemoryStore, workspace: Path, uploads: Path) -> None:
        stats = await sync_files(store, Embedder(), workspace, uploads_dir=uploads)
        assert stats["synced"] == 2

        files = {f["path"] for f in await store.list_files()}
        assert files == {"memory/note.md", "uploads/docs/doc1.txt"}

        texts = await _chunk_texts(store, "uploads/docs/doc1.txt")
        assert any("上传文档一的内容" in t for t in texts)

    async def test_removed_doc_cleaned(self, store: MemoryStore, workspace: Path, uploads: Path) -> None:
        await sync_files(store, Embedder(), workspace, uploads_dir=uploads)
        (uploads / "docs" / "doc1.txt").unlink()

        stats = await sync_files(store, Embedder(), workspace, uploads_dir=uploads)
        assert stats["removed"] == 1
        files = {f["path"] for f in await store.list_files()}
        assert files == {"memory/note.md"}
        assert await _chunk_texts(store, "uploads/docs/doc1.txt") == []

    async def test_docx_indexed(self, store: MemoryStore, workspace: Path, uploads: Path) -> None:
        _make_docx(uploads / "docs" / "report.docx", "季度报告正文内容")
        stats = await sync_files(store, Embedder(), workspace, uploads_dir=uploads)
        assert stats["synced"] == 3
        texts = await _chunk_texts(store, "uploads/docs/report.docx")
        assert any("季度报告正文内容" in t for t in texts)

    async def test_incremental_skip_unchanged(self, store: MemoryStore, workspace: Path, uploads: Path) -> None:
        await sync_files(store, Embedder(), workspace, uploads_dir=uploads)
        stats = await sync_files(store, Embedder(), workspace, uploads_dir=uploads)
        assert stats["synced"] == 0
