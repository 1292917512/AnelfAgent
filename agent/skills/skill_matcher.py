"""技能匹配器 — 将当前对话上下文匹配到相关技能。

双路评分：
- 关键词路：trigger_patterns 命中（精确 + 包含）
- 语义路：技能描述与查询文本的 embedding 相似度（Embedder 可用时）

匹配到的技能注入 volatile 层，供 AI 参考复用。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from agent.memory.memory_utils import cosine_similarity, hash_text
from agent.skills.skill_store import Skill, SkillState, SkillStore
from core.log import log

_W_KEYWORD = 0.4
_W_SEMANTIC = 0.6
_MIN_SCORE = 0.15

# 技能描述 embedding 缓存：按内容 hash 键控，内容变更即天然失效（新 hash 命中新键）。
# 容量有界（FIFO 淘汰），避免技能反复修改导致旧键只增不减。
_EMBEDDING_CACHE: Dict[str, List[float]] = {}
_EMBEDDING_CACHE_MAX = 512


def _cache_embedding(key: str, vec: List[float]) -> None:
    if len(_EMBEDDING_CACHE) >= _EMBEDDING_CACHE_MAX:
        oldest = next(iter(_EMBEDDING_CACHE))
        _EMBEDDING_CACHE.pop(oldest, None)
    _EMBEDDING_CACHE[key] = vec


def clear_embedding_cache() -> None:
    """清空技能 embedding 缓存（技能保存/更新后调用以确保不复用过期向量）。"""
    _EMBEDDING_CACHE.clear()


class SkillMatcher:
    """技能匹配：关键词 + 语义混合评分。"""

    def __init__(self, store: SkillStore, embedder: Optional[object] = None) -> None:
        self._store = store
        self._embedder = embedder
        # 技能列表缓存：按 store.version 失效，避免每轮匹配同步遍历目录读全部 SKILL.md
        self._list_cache: Optional[Tuple[int, List[Skill]]] = None

    def _list_matchable_skills(self) -> List[Skill]:
        """列出可匹配技能（ACTIVE/STALE），按版本号缓存。"""
        version = self._store.version
        if self._list_cache and self._list_cache[0] == version:
            return self._list_cache[1]
        skills = [
            s for s in self._store.list_skills()
            if s.state in (SkillState.ACTIVE, SkillState.STALE)
        ]
        self._list_cache = (version, skills)
        return skills

    async def match(
            self,
            query_texts: Sequence[str],
            *,
            top_k: int = 3,
            min_score: float = _MIN_SCORE,
            query_vec: Optional[List[float]] = None,
    ) -> List[Tuple[Skill, float]]:
        """匹配相关技能，返回 [(技能, 得分)] 按得分降序。

        Args:
            query_texts: 查询文本（通常为最近几条对话消息）
            top_k: 最多返回数量
            min_score: 最低得分阈值
            query_vec: 调用方预计算的查询向量（与记忆召回共享一次 embedding），
                为 None 时内部按需自行计算
        """
        skills = self._list_matchable_skills()
        if not skills or not query_texts:
            return []

        query = "\n".join(t for t in query_texts if t).strip()
        if not query:
            return []

        # 语义路：查询向量（embed_query 内部自带降级，不可用时返回 None 得 0 分；
        # 调用方已预计算则直接复用）
        if query_vec is None and self._embedder is not None:
            query_vec = await self._embedder.embed_query(query)  # type: ignore[attr-defined]

        scored: List[Tuple[Skill, float]] = []
        for skill in skills:
            score = self._keyword_score(skill, query) * _W_KEYWORD
            if query_vec is not None:
                skill_vec = await self._skill_embedding(skill)
                if skill_vec:
                    score += cosine_similarity(query_vec, skill_vec) * _W_SEMANTIC
            if score >= min_score:
                scored.append((skill, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        matched = scored[:top_k]
        if matched:
            names = ", ".join(f"{s.name}({score:.2f})" for s, score in matched)
            log(f"技能匹配: {names}", "DEBUG", tag="技能")
        return matched

    @staticmethod
    def _keyword_score(skill: Skill, query: str) -> float:
        """关键词得分：trigger_patterns 命中数 / 模式总数（上限 1.0）。"""
        if not skill.trigger_patterns:
            return 0.0
        query_lower = query.lower()
        hits = sum(
            1 for pattern in skill.trigger_patterns
            if pattern and pattern.lower() in query_lower
        )
        return min(1.0, hits / max(1, len(skill.trigger_patterns)) * 2)

    async def _skill_embedding(self, skill: Skill) -> Optional[List[float]]:
        """技能描述向量（按内容 hash 缓存，避免每轮实时重算）。"""
        embedder = self._embedder
        if embedder is None:
            return None
        text = f"{skill.name} {skill.description} {' '.join(skill.trigger_patterns)}"
        key = hash_text(text)
        cached = _EMBEDDING_CACHE.get(key)
        if cached is not None:
            return cached
        vec = await embedder.embed_query(text)  # type: ignore[attr-defined]
        if vec:
            _cache_embedding(key, vec)
        return vec
