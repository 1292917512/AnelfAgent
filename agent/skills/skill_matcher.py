"""技能匹配器 — 将当前对话上下文匹配到相关技能。

双路评分：
- 关键词路：trigger_patterns 命中（精确 + 包含）
- 语义路：技能描述与查询文本的 embedding 相似度（Embedder 可用时）

匹配到的技能注入 volatile 层，供 AI 参考复用。

近重复折叠：同簇冗余技能会在得分上互相接近，全部注入只会挤占 top-k 坑位、
重复消耗注入预算。选出结果前按向量相似度折叠近重复项（保留得分更高者），
折叠事件记入 index 的合并信号——是策展议程"该合并了"的直接证据。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from agent.memory.memory_utils import cosine_similarity
from agent.skills.skill_index import SkillIndex
from agent.skills.skill_store import Skill, SkillState, SkillStore
from core.log import log

_W_KEYWORD = 0.4
_W_SEMANTIC = 0.6
_MIN_SCORE = 0.15


def _redundancy_threshold() -> float:
    from core.config import get_config_float
    return get_config_float("skills_match_redundancy", 0.90)


class SkillMatcher:
    """技能匹配：关键词 + 语义混合评分 + 近重复折叠。"""

    def __init__(self, store: SkillStore, embedder: Optional[object] = None) -> None:
        self._store = store
        self._embedder = embedder
        # 事实索引：向量缓存/相似度的唯一权威，注入折叠的合并信号也记录于此
        self.index = SkillIndex(store, embedder)
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
            query_vec = await self.index.text_vector(query)

        # 技能向量预算化补算：embed_query 单条串行，全库冷缓存时逐个补算会拖垮
        # 首轮检索——预算内补算、其余本轮走关键词分，由心跳 warm() 批量预热
        vectors = (
            await self.index.ensure_vectors(skills)
            if query_vec is not None else {}
        )

        scored: List[Tuple[Skill, float]] = []
        for skill in skills:
            score = self._keyword_score(skill, query) * _W_KEYWORD
            if query_vec is not None:
                skill_vec = vectors.get(skill.name)
                if skill_vec:
                    score += cosine_similarity(query_vec, skill_vec) * _W_SEMANTIC
            if score >= min_score:
                scored.append((skill, score))

        scored.sort(key=lambda x: (x[1], x[0].name), reverse=True)
        matched = self._fold_redundant(scored, vectors, top_k)
        if matched:
            names = ", ".join(f"{s.name}({score:.2f})" for s, score in matched)
            log(f"技能匹配: {names}", "DEBUG", tag="技能")
        return matched

    def _fold_redundant(
            self,
            scored: List[Tuple[Skill, float]],
            vectors: dict[str, Optional[List[float]]],
            top_k: int,
    ) -> List[Tuple[Skill, float]]:
        """近重复折叠：与已保留者向量相似度过高的候选不再占用 top-k 坑位。

        折叠只依赖已有的语义向量（关键词路无向量时退化为不折叠）；
        每次折叠都记入 index 合并信号，成为后续策展的合并证据。
        """
        threshold = _redundancy_threshold()
        kept: List[Tuple[Skill, float]] = []
        kept_vecs: List[Tuple[Skill, List[float]]] = []
        for skill, score in scored:
            vec = vectors.get(skill.name)
            if vec is not None:
                fold_target = next(
                    (ks for ks, kv in kept_vecs
                     if cosine_similarity(vec, kv) >= threshold),
                    None,
                )
                if fold_target is not None:
                    self.index.record_merge_signal(fold_target.name, skill.name)
                    log(f"技能近重复折叠: {skill.name} → {fold_target.name}", "DEBUG", tag="技能")
                    continue
                kept_vecs.append((skill, vec))
            kept.append((skill, score))
            if len(kept) >= top_k:
                break
        return kept

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
