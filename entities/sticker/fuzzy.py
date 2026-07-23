"""模糊关键词打分：无 embedding 模型时的降级检索（移植自 hermes-agent 元宝贴纸检索）。

多级打分：精确匹配 > 子串 > 前缀 > 去标点子串 > 字符多重集命中率 > bigram Jaccard。
纯 Python 实现，对中文短查询（表情包场景）效果良好。
"""

from __future__ import annotations

import re
from collections import Counter

_PUNCT_RE = re.compile(r"[\s,，.。!！?？~～…、\-—_]+")


def _normalize(text: str) -> str:
    return _PUNCT_RE.sub("", (text or "").lower())


def _bigrams(text: str) -> set:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _longest_common_subsequence_ratio(a: str, b: str) -> float:
    """LCS 长度占查询长度的比例（O(len(a)*len(b))，仅用于短文本）。"""
    if not a or not b:
        return 0.0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[-1]))
        prev = cur
    return prev[-1] / len(a)


def _score_field(query: str, field: str) -> float:
    """对单个字段打分（0-100）。"""
    q = (query or "").strip().lower()
    f = (field or "").strip().lower()
    if not q or not f:
        return 0.0
    if q == f:
        return 100.0
    if q in f:
        return 92.0 + min(8.0, len(q) / max(len(f), 1) * 8)
    if f.startswith(q):
        return 88.0
    qn, fn = _normalize(q), _normalize(f)
    if qn and qn in fn:
        return 86.0
    # 字符多重集命中率
    q_counts = Counter(qn)
    f_counts = Counter(fn)
    if q_counts:
        hit = sum(min(c, f_counts.get(ch, 0)) for ch, c in q_counts.items())
        ratio = hit / sum(q_counts.values())
        if ratio > 0.5:
            return ratio * 62.0
    # bigram Jaccard
    qb, fb = _bigrams(qn), _bigrams(fn)
    if qb and fb:
        jaccard = len(qb & fb) / len(qb | fb)
        if jaccard > 0.2:
            return jaccard * 58.0
    # 最长公共子序列
    lcs = _longest_common_subsequence_ratio(qn, fn)
    if lcs > 0.6:
        return lcs * 52.0
    return 0.0


def fuzzy_score(query: str, name: str, description: str, tags: list[str]) -> float:
    """综合打分：名称权重最高，描述次之，标签再次。"""
    scores = [
        _score_field(query, name) * 1.0,
        _score_field(query, description) * 0.88,
        max((_score_field(query, t) for t in tags), default=0.0) * 0.8,
    ]
    return max(scores)


def fuzzy_rank(
    query: str,
    items: list[dict],
    *,
    limit: int = 5,
    min_score: float = 20.0,
) -> list[dict]:
    """对候选列表按模糊打分排序，返回带 score 的 Top-N。

    items 元素需含 name / description / tags(list) 键。
    """
    scored = []
    for item in items:
        score = fuzzy_score(
            query,
            item.get("name", ""),
            item.get("description", ""),
            item.get("tags") or [],
        )
        if score >= min_score:
            scored.append({**item, "score": round(score, 2)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
