"""记忆系统公共工具函数。

提取自 memory_store / memory_sync / embedder 中的共用逻辑，
消除模块间的私有函数依赖和重复实现。
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import numpy as np


def pack_embedding(vec: list[float]) -> bytes:
    """将浮点向量打包为二进制 blob。"""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes) -> list[float]:
    """将二进制 blob 解包为浮点向量。"""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度；空、异维、零模或非有限向量返回 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    left, right = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        denominator = np.linalg.norm(left) * np.linalg.norm(right)
        score = np.dot(left, right) / denominator if denominator > 0 else 0.0
    return float(np.clip(score, -1.0, 1.0)) if np.isfinite(score) else 0.0


def rank_embedding_blobs(
    query_vec: list[float], blobs: list[bytes | None], *, limit: int, min_score: float,
) -> list[tuple[int, float]]:
    """批量精确评分，返回 (原位置, 分数) 前 limit 项，同分保留输入顺序。

    BLOB 只建立 float32 视图，累计使用 float64；不逐行构造 Python 浮点列表。
    损坏、异维或非有限向量不作为证据，零模向量得分为 0。
    调用方按页传入，内存随页大小而非库大小增长。
    """
    if not query_vec or limit <= 0:
        return []
    query = np.asarray(query_vec, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        query_norm = np.linalg.norm(query)
    if not np.isfinite(query_norm):
        return []
    vectors = [(i, np.frombuffer(blob, dtype=np.float32)) for i, blob in enumerate(blobs)
               if blob is not None and len(blob) == len(query_vec) * 4]
    if not vectors:
        return []
    matrix = np.stack([vector for _, vector in vectors]).astype(np.float64)
    # einsum 避免小批次触发多线程 BLAS，与其它召回线程争抢计算资源。
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        dots = np.einsum("ij,j->i", matrix, query)
        norms = np.sqrt(np.einsum("ij,ij->i", matrix, matrix)) * query_norm
        scores = np.divide(dots, norms, out=np.zeros(len(vectors)), where=norms > 0)
    valid = np.isfinite(dots) & np.isfinite(norms)
    np.clip(scores, -1.0, 1.0, out=scores)
    hits = np.flatnonzero(valid & (scores >= min_score))
    ranked = hits[np.argsort(-scores[hits], kind="stable")[:limit]]
    return [(vectors[int(i)][0], float(scores[i])) for i in ranked]


def hash_text(text: str) -> str:
    """对文本内容计算 SHA-256 哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_memory_md_files(memory_dir: Path) -> list[Path]:
    """扫描记忆数据目录内的 Markdown 文件，排除越界符号链接。"""
    if not memory_dir.is_dir():
        return []
    return sorted(
        p for p in memory_dir.rglob("*.md")
        if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(memory_dir.resolve())
    )


def list_indexable_files(
    memory_dir: Path,
    uploads_dir: Path | None = None,
) -> list[tuple[Path, str]]:
    """枚举全部可索引文件，返回 (绝对路径, 索引 rel_key) 对。

    rel_key 命名空间：
    - memory/**/*.md → 记忆数据目录内的路径，统一使用 memory/ 索引前缀
    - uploads/docs/** → "uploads/docs/<相对路径>"（文档命名空间）
    """
    from .doc_extract import SUPPORTED_DOC_EXTS

    pairs: list[tuple[Path, str]] = [
        (p, f"memory/{p.relative_to(memory_dir).as_posix()}")
        for p in list_memory_md_files(memory_dir)
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
