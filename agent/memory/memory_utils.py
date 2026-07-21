"""记忆系统公共工具函数。

提取自 memory_store / memory_sync / embedder 中的共用逻辑，
消除模块间的私有函数依赖和重复实现。
"""

from __future__ import annotations

import hashlib
import math
import struct
from pathlib import Path


def pack_embedding(vec: list[float]) -> bytes:
    """将浮点向量打包为二进制 blob。"""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes) -> list[float]:
    """将二进制 blob 解包为浮点向量。"""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def hash_text(text: str) -> str:
    """对文本内容计算 SHA-256 哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_workspace_md_files(workspace_dir: Path) -> list[Path]:
    """扫描 workspace/memory/ 下的所有 .md 记忆文件。"""
    memory_dir = workspace_dir / "memory"
    if not memory_dir.is_dir():
        return []
    return sorted(
        p for p in memory_dir.rglob("*.md")
        if p.is_file() and not p.is_symlink()
    )


def list_indexable_files(
    workspace_dir: Path,
    uploads_dir: Path | None = None,
) -> list[tuple[Path, str]]:
    """枚举全部可索引文件，返回 (绝对路径, 索引 rel_key) 对。

    rel_key 命名空间：
    - memory/**/*.md → 相对 workspace_dir 的路径（兼容存量索引）
    - uploads/docs/** → "uploads/docs/<相对路径>"（文档命名空间）
    """
    from .doc_extract import SUPPORTED_DOC_EXTS

    pairs: list[tuple[Path, str]] = [
        (p, str(p.relative_to(workspace_dir)).replace("\\", "/"))
        for p in list_workspace_md_files(workspace_dir)
    ]
    if uploads_dir is not None:
        docs_dir = uploads_dir / "docs"
        if docs_dir.is_dir():
            for p in sorted(docs_dir.rglob("*")):
                if (
                    p.is_file()
                    and not p.is_symlink()
                    and p.suffix.lower() in SUPPORTED_DOC_EXTS
                ):
                    rel = str(p.relative_to(uploads_dir)).replace("\\", "/")
                    pairs.append((p, f"uploads/{rel}"))
    return pairs
