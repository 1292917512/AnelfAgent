"""声纹向量代数：余弦、时长加权与质心合成（numpy 向量化）。

锚（anchor）是说话人声纹的代表向量——全部历史合格样本的加权质心。
加权质心满足结合律：任意顺序、任意分组折叠的结果与一次性全量加权
平均一致，因此锚可随采样增量进化、可跨档案精确合并，无需重放历史。
批量接口（cosine_many / pairwise_sims）走单次矩阵运算，说话人量级的
全库扫描在微秒级完成。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

Vec = Sequence[float]


def _row_matrix(vectors: Sequence[Vec]) -> Optional[np.ndarray]:
    """向量组 → [N, D] float64 矩阵；维度不一致的行剔除，空组返回 None。"""
    rows = [np.asarray(v, dtype=np.float64) for v in vectors if len(v)]
    if not rows:
        return None
    dims = rows[0].shape[0]
    matrix = np.stack([r for r in rows if r.shape[0] == dims])
    return matrix if len(matrix) else None


def cosine(a: Vec, b: Vec) -> float:
    vec_a = np.asarray(a, dtype=np.float64)
    vec_b = np.asarray(b, dtype=np.float64)
    na = float(np.linalg.norm(vec_a))
    nb = float(np.linalg.norm(vec_b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(vec_a @ vec_b / (na * nb))


def cosine_many(matrix: np.ndarray, vector: Vec) -> np.ndarray:
    """矩阵各行与向量的余弦（[N,D]×[D] → [N]；零向量行得 0）。"""
    if matrix.size == 0:
        return np.zeros(0)
    vec = np.asarray(vector, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1) * float(np.linalg.norm(vec))
    dots = matrix @ vec
    sims = np.zeros(len(matrix))
    ok = norms > 0
    sims[ok] = dots[ok] / norms[ok]
    return sims


def unit_rows(matrix: np.ndarray) -> np.ndarray:
    """行归一化（零行保持零，其相似度自然为 0）。"""
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


def pairwise_sims(matrix: np.ndarray) -> np.ndarray:
    """行归一化矩阵的两两余弦 [N,N]（聚类全量相似度用）。"""
    unit = unit_rows(matrix)
    return unit @ unit.T


def sample_weight(duration_ms: int) -> float:
    """样本权重按语音时长计：长语音的声纹嵌入更稳定，截断到 [0.5, 10] 秒。"""
    return min(10.0, max(0.5, duration_ms / 1000.0))


def blend(a: Vec, wa: float, b: Vec, wb: float) -> Tuple[List[float], float]:
    """合成两团加权向量（增量折叠 / 档案合并的同一代数）。"""
    total = wa + wb
    if total <= 0:
        return list(a), 0.0
    merged = (wa * np.asarray(a, dtype=np.float64) + wb * np.asarray(b, dtype=np.float64)) / total
    return merged.tolist(), total


def weighted_centroid(
    pairs: Sequence[Tuple[Vec, float]],
) -> Optional[Tuple[List[float], float]]:
    """样本集的加权质心；空集返回 None。"""
    vectors = [v for v, _ in pairs if len(v)]
    weights = np.asarray([w for v, w in pairs if len(v)], dtype=np.float64)
    matrix = _row_matrix(vectors)
    if matrix is None or not len(weights) or weights.sum() <= 0:
        return None
    total = float(weights.sum())
    centroid = (weights @ matrix) / total
    return centroid.tolist(), total
