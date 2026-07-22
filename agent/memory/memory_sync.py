"""记忆文件索引同步：扫描 memory/*.md + uploads/docs 文档，增量分块并生成 embedding 索引。

参考 OpenClaw 的 manager-sync-ops.ts / internal.ts 实现。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from .doc_extract import extract_document_text
from .embedder import Embedder
from .memory_store import MemoryStore
from .memory_utils import hash_text, list_indexable_files, pack_embedding

# 分块参数（参考 OpenClaw 默认值）
DEFAULT_CHUNK_TOKENS = 400
DEFAULT_CHUNK_OVERLAP = 80
_CHARS_PER_TOKEN = 3
# 切块器版本：算法变更时递增，触发既有索引整体重建
_CHUNKER_VERSION = 2

_tiktoken_encoder: Any = None
_tiktoken_failed = False


def _get_encoder() -> Any:
    """惰性加载 cl100k_base 编码器，不可用时返回 None 走字符估算。"""
    global _tiktoken_encoder, _tiktoken_failed
    if _tiktoken_encoder is not None or _tiktoken_failed:
        return _tiktoken_encoder
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        log(f"tiktoken 不可用，降级为字符估算切块: {exc}", "WARNING", tag="思维")
        _tiktoken_failed = True
    return _tiktoken_encoder


def _line_units(lines: list[str]) -> list[int]:
    """逐行计算 token 数（含换行符 1 token）；无编码器时按字符估算。"""
    encoder = _get_encoder()
    if encoder is None:
        return [max(1, (len(line) + _CHARS_PER_TOKEN) // _CHARS_PER_TOKEN) for line in lines]
    return [len(encoder.encode(line)) + 1 for line in lines]


def chunk_markdown(
    content: str,
    *,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Dict[str, Any]]:
    """将 Markdown 内容按 token 限制分块，返回 [{start_line, end_line, text, hash}]。"""
    lines = content.split("\n")
    if not lines:
        return []

    max_tokens = max(32, chunk_tokens)
    overlap_tokens = min(max(0, overlap_tokens), max_tokens // 2)
    line_units = _line_units(lines)

    chunks: list[Dict[str, Any]] = []
    current: list[tuple[str, int, int]] = []
    current_tokens = 0

    def flush() -> None:
        if not current:
            return
        text = "\n".join(line for line, _, _ in current)
        chunks.append({
            "start_line": current[0][1],
            "end_line": current[-1][1],
            "text": text,
            "hash": hash_text(text),
        })

    def carry_overlap() -> tuple[list[tuple[str, int, int]], int]:
        if overlap_tokens <= 0 or not current:
            return [], 0
        kept: list[tuple[str, int, int]] = []
        acc = 0
        for item in reversed(current):
            acc += item[2]
            kept.insert(0, item)
            if acc >= overlap_tokens:
                break
        return kept, sum(u for _, _, u in kept)

    for i, line in enumerate(lines):
        line_no = i + 1
        line_size = line_units[i]

        if current_tokens + line_size > max_tokens and current:
            flush()
            current, current_tokens = carry_overlap()
            # 重叠行与入行放不下时放弃重叠，保证块不超限
            if current and current_tokens + line_size > max_tokens:
                current, current_tokens = [], 0

        current.append((line, line_no, line_size))
        current_tokens += line_size

    flush()
    return chunks


def _default_uploads_dir() -> Path:
    from core.path import ConfigPaths, project_root
    return Path(project_root()) / ConfigPaths.UPLOAD_DIR


def _read_indexable_content(file_path: Path) -> str:
    """按扩展名读取可索引内容：PDF/DOCX 走文本提取，其余按纯文本读取。"""
    if file_path.suffix.lower() in (".pdf", ".docx"):
        return extract_document_text(file_path)
    return file_path.read_text(encoding="utf-8")


async def index_single_file(
    store: MemoryStore,
    embedder: Embedder,
    file_path: Path,
    rel_path: str,
    *,
    force: bool = False,
    known_hash: Optional[str] = None,
) -> int:
    """索引单个文件（md/pdf/docx/txt），返回写入 chunk 数；内容未变化返回 0。"""
    try:
        stat = file_path.stat()
        content = _read_indexable_content(file_path)
    except Exception as exc:
        log(f"跳过文件 {rel_path}: {exc}", "WARNING", tag="思维")
        return 0

    file_hash = hash_text(f"v{_CHUNKER_VERSION}:{content}")
    if not force:
        if known_hash is None:
            existing = await store.get_file(rel_path)
            known_hash = existing["hash"] if existing else None
        if known_hash == file_hash:
            return 0

    chunks = chunk_markdown(content)
    if not chunks:
        return 0

    chunk_dicts: list[Dict[str, Any]] = []
    now_ns = int(time.time() * 1e9)

    for ch in chunks:
        chunk_id = f"{rel_path}:{ch['start_line']}-{ch['end_line']}"
        embedding_blob: Optional[bytes] = None

        # 只命中缓存即落库；未命中的留 NULL，由后台 EmbeddingWorker 批量补全
        cached = await store.get_cached_embedding(ch["hash"])
        if cached:
            embedding_blob = pack_embedding(cached)

        chunk_dicts.append({
            "id": chunk_id,
            "path": rel_path,
            "start_line": ch["start_line"],
            "end_line": ch["end_line"],
            "hash": ch["hash"],
            "text": ch["text"],
            "embedding": embedding_blob,
            "updated_ns": now_ns,
        })

    await store.delete_chunks_by_path(rel_path)
    written = await store.upsert_chunks(chunk_dicts)
    await store.upsert_file(rel_path, file_hash, int(stat.st_mtime * 1e9), stat.st_size)
    log(f"📄 索引文件: {rel_path} → {written} chunks", tag="思维")
    return written


async def sync_files(
    store: MemoryStore,
    embedder: Embedder,
    workspace_dir: Path,
    *,
    force: bool = False,
    uploads_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """增量同步记忆文件与上传文档到索引。

    对比 files 表中的 hash，只处理新增/变化的文件。
    返回 {"synced": N, "removed": M, "chunks": C} 统计。
    """
    if uploads_dir is None:
        uploads_dir = _default_uploads_dir()
    files = list_indexable_files(workspace_dir, uploads_dir)
    stats = {"synced": 0, "removed": 0, "chunks": 0}

    indexed_files = {f["path"]: f for f in await store.list_files()}
    current_paths: set[str] = set()

    for file_path, rel_path in files:
        current_paths.add(rel_path)
        existing = indexed_files.get(rel_path)
        written = await index_single_file(
            store, embedder, file_path, rel_path,
            force=force,
            known_hash=existing["hash"] if existing else None,
        )
        if written:
            stats["synced"] += 1
            stats["chunks"] += written

    for old_path in indexed_files:
        if old_path not in current_paths:
            await store.delete_file(old_path)
            stats["removed"] += 1
            log(f"🗑️ 移除索引: {old_path}", tag="思维")

    if stats["synced"] or stats["removed"]:
        log(
            f"📊 文件同步完成: 同步 {stats['synced']} 文件, "
            f"移除 {stats['removed']} 文件, 共 {stats['chunks']} chunks",
            tag="思维",
        )
        if stats["chunks"]:
            from .embedding_worker import wake_embedding_worker
            wake_embedding_worker()

    return stats
