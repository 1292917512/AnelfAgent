"""声纹向量代数：余弦、时长加权与质心合成。

锚（anchor）是说话人声纹的代表向量——全部历史合格样本的加权质心。
加权质心满足结合律：任意顺序、任意分组折叠的结果与一次性全量加权
平均一致，因此锚可随采样增量进化、可跨档案精确合并，无需重放历史。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Vec = Sequence[float]


def cosine(a: Vec, b: Vec) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def sample_weight(duration_ms: int) -> float:
    """样本权重按语音时长计：长语音的声纹嵌入更稳定，截断到 [0.5, 10] 秒。"""
    return min(10.0, max(0.5, duration_ms / 1000.0))


def blend(a: Vec, wa: float, b: Vec, wb: float) -> Tuple[List[float], float]:
    """合成两团加权向量（增量折叠 / 档案合并的同一代数）。"""
    total = wa + wb
    if total <= 0:
        return list(a), 0.0
    return [(wa * x + wb * y) / total for x, y in zip(a, b, strict=False)], total


def weighted_centroid(
    pairs: Sequence[Tuple[Vec, float]],
) -> Optional[Tuple[List[float], float]]:
    """样本集的加权质心；空集返回 None。"""
    if not pairs:
        return None
    acc = [0.0] * len(pairs[0][0])
    total = 0.0
    for vec, weight in pairs:
        for i, x in enumerate(vec):
            acc[i] += weight * x
        total += weight
    if total <= 0:
        return None
    return [x / total for x in acc], total
