"""防复读：近期回复的话题新鲜度纪律（软提示）。

生成侧软引导（build_repeat_hint → context 管线 freshness 块）：统计当前会话
最近 N 条 AI 回复里反复出现的完整话题词，注入提示引导生成侧换角度。覆盖
全部回复周期——包括提醒/任务触发的 REPLY（提醒必须送达，只能在生成侧
引导，不能拦）。

话题候选来自单条回复的 TF-IDF 关键词，按涉及的回复数和关键词权重排序。
话题词仅在动态尾部提示中展示，最多 top_k 个，不修改前缀或增加模型调用。
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from typing import TYPE_CHECKING

from core.config import (
    get_config_bool,
    get_config_int,
    register_configs_safe,
)
from core.sanitizer import truncate_middle
from core.tags import strip_functional_tags, strip_message_meta_tags

if TYPE_CHECKING:
    from agent.storage.sqlite_backend import SqliteBackend

_REPEAT_CONFIGS = {
    "memory/anti_repeat": {
        "memory_repeat_hint_enabled": {
            "description": "是否注入话题新鲜度提示（最近回复的高频话题提醒）",
            "default": True,
        },
        "memory_repeat_hint_top_k": {
            "description": "软提示最多列出的话题词数",
            "default": 5,
            "advanced": True,
            "unit": "个",
        },
        "memory_repeat_hint_min_df": {
            "description": "话题词需在至少多少条近期回复中出现过",
            "default": 3,
            "advanced": True,
            "unit": "条",
        },
        "memory_repeat_window": {
            "description": "统计话题的近期 AI 回复条数",
            "default": 20,
            "advanced": True,
            "unit": "条",
        },
    },
}

register_configs_safe(_REPEAT_CONFIGS)

_OPENING = re.compile(r"^([\w \t]+)[^\w\s]*[,，]\s*(?=\S)")
_CONTENT_POS = ("n", "nr", "ns", "nt", "nz", "vn", "v", "a", "eng")


@lru_cache(maxsize=128)
def _reply_keywords(body: str) -> tuple[tuple[str, float], ...]:
    """缓存有界单条正文的关键词；历史编辑和统计配置变化无需清缓存。"""
    from jieba.analyse import extract_tags

    return tuple(extract_tags(body, topK=None, withWeight=True, allowPOS=_CONTENT_POS))


def compute_hint_terms(recent_texts: Sequence[str], *, top_k: int, min_df: int) -> list[str]:
    """按独立回复计数；跨回复重复的逗号开场白仅作行文格式，正文里的同词照常参与。"""
    if len(recent_texts) < min_df:
        return []
    bodies = [
        truncate_middle(strip_functional_tags(strip_message_meta_tags(text)).strip().casefold(), 2048, marker="\n")
        for text in recent_texts
    ]
    openings = [_OPENING.match(body) for body in bodies]
    repeated = Counter(match[1].strip() for match in openings if match)
    frequency: Counter[str] = Counter()
    weights: dict[str, float] = {}
    for body, opening in zip(bodies, openings, strict=True):
        keywords = dict(_reply_keywords(body))
        if opening:
            lead = opening[1].strip()
            if repeated[lead] >= min_df and lead not in body[opening.end():]:
                # 仅折叠自身就是一个关键词的重复称呼，保留整句开场中的实词。
                keywords.pop(lead, None)
        for term, weight in keywords.items():
            frequency[term] += 1
            weights[term] = weights.get(term, 0.0) + weight
    eligible = (term for term, count in frequency.items() if count >= min_df)
    return sorted(eligible, key=lambda term: (-frequency[term], -weights[term], term))[:top_k]


async def build_repeat_hint(sqlite: SqliteBackend, *, scope_type: str, scope_id: str) -> str:
    """只读取当前会话的近期助手回复；计算在工作线程执行。"""
    if not get_config_bool("memory_repeat_hint_enabled", True):
        return ""
    rows = await sqlite.fetch_conversation(
        scope_type=scope_type, scope_id=scope_id, role="assistant", limit=max(1, get_config_int("memory_repeat_window", 20)),
    )
    terms = await asyncio.to_thread(
        compute_hint_terms, [row["content"] for row in rows],
        top_k=max(1, get_config_int("memory_repeat_hint_top_k", 5)),
        min_df=max(2, get_config_int("memory_repeat_hint_min_df", 3)),
    )
    return (
        "[系统注入·话题新鲜度] 最近几条回复已反复谈及："
        + "、".join(terms)
        + "。本次回复注意换新角度或新话题，除非对方主动回到这些话题。"
        if terms else ""
    )
