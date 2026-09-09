"""密码本模糊检索：多级打分（移植自 entities/sticker/fuzzy.py，字段权重适配密码本场景）。

多级打分：精确匹配 > 子串 > 前缀 > 去标点子串 > 字符多重集命中率 > bigram Jaccard > LCS。
URL 归一化（去 scheme/www/尾斜杠）使 "github.com" 命中 "https://www.github.com/login"。
条目量级为百级，纯 Python 打分毫秒级，无需 FTS5。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

_PUNCT_RE = re.compile(r"[\s,，.。!！?？~～…、\-—_]+")
_URL_PREFIX_RE = re.compile(r"^(?:https?://)?(?:www\.)?")


def normalize_url(url: str) -> str:
    """URL 归一化：去 scheme / www / 尾斜杠，便于站点名直接命中。"""
    text = (url or "").strip().lower()
    text = _URL_PREFIX_RE.sub("", text)
    return text.rstrip("/")


def _normalize(text: str) -> str:
    return _PUNCT_RE.sub("", (text or "").lower())


def _bigrams(text: str) -> set:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _lcs_ratio(a: str, b: str) -> float:
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
    q_counts = Counter(qn)
    f_counts = Counter(fn)
    if q_counts:
        hit = sum(min(c, f_counts.get(ch, 0)) for ch, c in q_counts.items())
        ratio = hit / sum(q_counts.values())
        if ratio > 0.5:
            return ratio * 62.0
    qb, fb = _bigrams(qn), _bigrams(fn)
    if qb and fb:
        jaccard = len(qb & fb) / len(qb | fb)
        if jaccard > 0.2:
            return jaccard * 58.0
    lcs = _lcs_ratio(qn, fn)
    if lcs > 0.6:
        return lcs * 52.0
    return 0.0


def entry_score(query: str, entry: Dict[str, Any]) -> float:
    """条目综合打分：标题 > 账号 > 站点 URL（归一化 + 原文） > 标签。"""
    url = entry.get("url", "")
    scores = [
        _score_field(query, entry.get("title", "")) * 1.0,
        _score_field(query, entry.get("username", "")) * 0.9,
        max(
            _score_field(query, url),
            _score_field(normalize_url(query), normalize_url(url)),
        ) * 0.85,
        max((_score_field(query, t) for t in entry.get("tags") or []), default=0.0) * 0.8,
    ]
    return max(scores)


def rank_entries(
    query: str,
    entries: List[Dict[str, Any]],
    *,
    limit: int = 10,
    min_score: float = 20.0,
) -> List[Dict[str, Any]]:
    """按模糊打分排序，返回带 score 的 Top-N 条目。"""
    scored = []
    for entry in entries:
        score = entry_score(query, entry)
        if score >= min_score:
            scored.append({**entry, "score": round(score, 2)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
