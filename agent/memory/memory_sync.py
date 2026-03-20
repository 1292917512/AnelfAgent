"""MD 文件索引同步：扫描 memory.md + memory/*.md，增量分块并生成 embedding 索引。

参考 OpenClaw 的 manager-sync-ops.ts / internal.ts 实现。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from .embedder import Embedder
from .memory_store import MemoryStore
from .memory_utils import hash_text, list_workspace_md_files, pack_embedding

# 分块参数（参考 OpenClaw 默认值）
DEFAULT_CHUNK_TOKENS = 400
DEFAULT_CHUNK_OVERLAP = 80
_CHARS_PER_TOKEN = 3


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

    max_chars = max(32, chunk_tokens * _CHARS_PER_TOKEN)
    overlap_chars = max(0, overlap_tokens * _CHARS_PER_TOKEN)

    chunks: list[Dict[str, Any]] = []
    current: list[tuple[str, int]] = []
    current_chars = 0

    def flush() -> None:
        if not current:
            return
        text = "\n".join(line for line, _ in current)
        start_line = current[0][1]
        end_line = current[-1][1]
        chunks.append({
            "start_line": start_line,
            "end_line": end_line,
            "text": text,
            "hash": hash_text(text),
        })

    def carry_overlap() -> tuple[list[tuple[str, int]], int]:
        if overlap_chars <= 0 or not current:
            return [], 0
        kept: list[tuple[str, int]] = []
        acc = 0
        for line_text, line_no in reversed(current):
            acc += len(line_text) + 1
            kept.insert(0, (line_text, line_no))
            if acc >= overlap_chars:
                break
        new_chars = sum(len(t) + 1 for t, _ in kept)
        return kept, new_chars

    for i, line in enumerate(lines):
        line_no = i + 1
        line_size = len(line) + 1

        if current_chars + line_size > max_chars and current:
            flush()
            current, current_chars = carry_overlap()

        current.append((line, line_no))
        current_chars += line_size

    flush()
    return chunks


async def sync_files(
    store: MemoryStore,
    embedder: Embedder,
    workspace_dir: Path,
    *,
    force: bool = False,
) -> Dict[str, int]:
    """增量同步记忆文件到索引。

    对比 files 表中的 hash，只处理新增/变化的文件。
    返回 {"synced": N, "removed": M, "chunks": C} 统计。
    """
    files = list_workspace_md_files(workspace_dir)
    stats = {"synced": 0, "removed": 0, "chunks": 0}

    indexed_files = {f["path"]: f for f in await store.list_files()}
    current_paths: set[str] = set()

    for file_path in files:
        rel_path = str(file_path.relative_to(workspace_dir)).replace("\\", "/")
        current_paths.add(rel_path)

        try:
            stat = file_path.stat()
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            log(f"跳过文件 {rel_path}: {exc}", "WARNING", tag="思维")
            continue

        file_hash = hash_text(content)
        mtime_ns = int(stat.st_mtime * 1e9)
        size = stat.st_size

        existing = indexed_files.get(rel_path)
        if existing and existing["hash"] == file_hash and not force:
            continue

        chunks = chunk_markdown(content)
        if not chunks:
            continue

        chunk_dicts: list[Dict[str, Any]] = []
        now_ns = int(time.time() * 1e9)

        for ch in chunks:
            chunk_id = f"{rel_path}:{ch['start_line']}-{ch['end_line']}"
            embedding_blob: Optional[bytes] = None

            cached = await store.get_cached_embedding(ch["hash"])
            if cached:
                embedding_blob = pack_embedding(cached)
            elif embedder.available:
                vec = await embedder.embed_one(ch["text"])
                if vec:
                    embedding_blob = pack_embedding(vec)
                    await store.put_cached_embedding(ch["hash"], vec)

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
        stats["chunks"] += written

        await store.upsert_file(rel_path, file_hash, mtime_ns, size)
        stats["synced"] += 1
        log(f"📄 索引文件: {rel_path} → {written} chunks", tag="思维")

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

    return stats
