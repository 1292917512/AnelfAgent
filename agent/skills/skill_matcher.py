"""技能匹配器 — 将当前对话上下文匹配到相关技能。

匹配面 = 全量技能（含归档）：归档是库容卫生措施而非删除，其方法仍有召回
价值——归档命中带惩罚系数降权，注入时标注状态，AI 可经 restore_skill 恢复。
（skills_match_include_archived 可关闭归档旁路。）

多查询车道 + 双源关键词 + 语义混合评分：
- 查询车道：对话尾部基查询之外，可叠加记忆召回规划产出的互补查询（共享
  Embedder 查询缓存，零额外嵌入成本）；每条车道独立评分，同一技能取最高分
- 关键词路：trigger_patterns 命中（×2 加成，手工声明的高精度信号）与
  技能名 kebab 分词 token 命中（零维护的自给信号）取 max
- 语义路：技能描述与查询文本的 embedding 相似度（Embedder 可用时）

匹配到的技能注入 volatile 层，供 AI 参考复用。

近重复折叠：同簇冗余技能会在得分上互相接近，全部注入只会挤占 top-k 坑位、
重复消耗注入预算。选出结果前按向量相似度折叠近重复项（保留得分更高者），
折叠事件记入 index 的合并信号——是策展议程"该合并了"的直接证据。
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from agent.memory.memory_utils import cosine_similarity
from agent.skills.skill_index import SkillIndex
from agent.skills.skill_store import Skill, SkillState, SkillStore
from core.log import log


def _min_score() -> float:
    from core.config import get_config_float
    return get_config_float("skills_match_min_score", 0.15)


def _keyword_weight() -> float:
    from core.config import get_config_float
    return get_config_float("skills_match_keyword_weight", 0.4)


def _include_archived() -> bool:
    from core.config import get_config_bool
    return get_config_bool("skills_match_include_archived", True)


def _archived_factor() -> float:
    from core.config import get_config_float
    return get_config_float("skills_match_archived_factor", 0.8)


def _redundancy_threshold() -> float:
    from core.config import get_config_float
    return get_config_float("skills_match_redundancy", 0.90)


def _log_path_divergence(
        matched: List[Tuple["Skill", float]],
        keyword_ranked: List[Tuple[float, str]],
        top_k: int,
) -> None:
    """双路分歧观测：混合评分入选挤掉了纯关键词路 top-k 技能时记录。

    分歧日志是权重调优（关键词 / 语义配比）的实证依据：
    关键词命中的技能被语义分挤出注入位 = 触发词信号被淹没的直接证据。
    """
    ranked = sorted(keyword_ranked, reverse=True)
    keyword_top = {name for score, name in ranked[:top_k] if score > 0}
    mixed_top = {skill.name for skill, _ in matched}
    displaced = keyword_top - mixed_top
    if displaced:
        log(
            f"技能双路分歧: 关键词路 {sorted(keyword_top)} 中 {sorted(displaced)} "
            f"被语义分挤出，混合入选 {sorted(mixed_top)}",
            "DEBUG", tag="技能",
        )


class SkillMatcher:
    """技能匹配：多车道混合评分 + 近重复折叠。"""

    def __init__(self, store: SkillStore, embedder: Optional[object] = None) -> None:
        self._store = store
        self._embedder = embedder
        # 事实索引：向量缓存/相似度/技能列表缓存的唯一权威，
        # 注入折叠的合并信号也记录于此
        self.index = SkillIndex(store, embedder)

    async def match(
            self,
            queries: Sequence[str],
            *,
            top_k: int = 3,
            min_score: Optional[float] = None,
            query_vec: Optional[List[float]] = None,
    ) -> List[Tuple[Skill, float]]:
        """匹配相关技能，返回 [(技能, 得分)] 按得分降序。

        Args:
            queries: 查询车道（对话尾部基查询 + 记忆召回规划的互补查询）；
                每条车道独立评分，同一技能取各车道最高分
            top_k: 最多返回数量
            min_score: 最低得分阈值（None 读配置 skills_match_min_score）
            query_vec: 首条车道的预计算查询向量（与记忆召回共享一次 embedding），
                为 None 时内部按需自行计算
        """
        skills = self.index.match_surface(include_archived=_include_archived())
        lanes = [q.strip() for q in queries if q and q.strip()]
        if not skills or not lanes:
            return []
        if min_score is None:
            min_score = _min_score()

        # 各车道查询向量：首条复用调用方预计算，其余按需嵌入
        # （记忆召回刚嵌入过规划查询时命中 Embedder 查询缓存，零额外成本）；
        # embed_query 内部自带降级，不可用时返回 None 该车道语义路得 0 分
        lane_vecs: List[Optional[List[float]]] = []
        for i, lane in enumerate(lanes):
            if i == 0 and query_vec is not None:
                lane_vecs.append(query_vec)
            else:
                lane_vecs.append(await self.index.text_vector(lane))

        # 技能向量预算化补算：embed_query 单条串行，全库冷缓存时逐个补算会拖垮
        # 首轮检索——预算内补算、其余本轮走关键词分，由心跳 warm() 批量预热
        vectors = (
            await self.index.ensure_vectors(skills)
            if any(v is not None for v in lane_vecs) else {}
        )

        kw = _keyword_weight()
        sw = 1.0 - kw
        factor = _archived_factor()
        scored: List[Tuple[Skill, float]] = []
        keyword_ranked: List[Tuple[float, str]] = []
        for skill in skills:
            skill_vec = vectors.get(skill.name)
            score = 0.0
            keyword_best = 0.0
            for lane, lane_vec in zip(lanes, lane_vecs, strict=True):
                keyword = self._keyword_score(skill, lane)
                keyword_best = max(keyword_best, keyword)
                lane_score = keyword * kw
                if lane_vec is not None and skill_vec:
                    lane_score += cosine_similarity(lane_vec, skill_vec) * sw
                score = max(score, lane_score)
            keyword_ranked.append((keyword_best, skill.name))
            if skill.state is SkillState.ARCHIVED:
                score *= factor
            if score >= min_score:
                scored.append((skill, score))

        scored.sort(key=lambda x: (x[1], x[0].name), reverse=True)
        matched = self._fold_redundant(scored, vectors, top_k)
        if matched:
            names = ", ".join(f"{s.name}({score:.2f})" for s, score in matched)
            log(f"技能匹配: {names}", "DEBUG", tag="技能")
            _log_path_divergence(matched, keyword_ranked, top_k)
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
    def _name_tokens(name: str) -> List[str]:
        """技能名 kebab 分词（≥3 字符 token）：零维护的关键词信号源。

        中文语料里用户写出拉丁 token（comfyui/blender/mmd 等）时几乎必为
        话题相关；单 token 命中得分天然低于阈值，需多 token 或语义路共振。
        """
        return [t for t in re.split(r"[-_]+", name.lower()) if len(t) >= 3]

    @classmethod
    def _keyword_score(cls, skill: Skill, query: str) -> float:
        """关键词得分：trigger_patterns（×2 加成）与名称 token（命中率）取 max。"""
        query_lower = query.lower()
        score = 0.0
        patterns = skill.trigger_patterns
        if patterns:
            hits = sum(
                1 for pattern in patterns
                if pattern and pattern.lower() in query_lower
            )
            score = min(1.0, hits / max(1, len(patterns)) * 2)
        tokens = cls._name_tokens(skill.name)
        if tokens:
            hits = sum(1 for token in tokens if token in query_lower)
            score = max(score, hits / len(tokens))
        return score


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

from core.config import register_configs_safe  # noqa: E402

register_configs_safe({"skills/match": {
    "skills_match_min_score": {
        "description": "技能匹配注入的最低得分（关键词×权重 + 语义余弦×权重的混合分）",
        "default": 0.15,
        "advanced": True,
    },
    "skills_match_keyword_weight": {
        "description": "关键词路在混合评分中的权重（语义路权重 = 1 - 本值）",
        "default": 0.4,
        "advanced": True,
    },
    "skills_match_include_archived": {
        "description": "归档技能参与匹配（带惩罚系数降权，命中标注已归档可恢复）："
                       "归档是库容卫生而非删除，其方法保留召回价值",
        "default": True,
    },
    "skills_match_archived_factor": {
        "description": "归档技能命中的得分惩罚系数（0~1，越小越难盖过在役技能）",
        "default": 0.8,
        "advanced": True,
    },
    "skills_match_top_k": {
        "description": "技能匹配注入的最大数量",
        "default": 3,
        "advanced": True,
        "unit": "个",
    },
    "skills_match_redundancy": {
        "description": "检索注入的近重复折叠阈值：候选与已入选技能相似度超过该值时折叠"
                       "（保留得分更高者，折叠记入合并信号）",
        "default": 0.9,
        "advanced": True,
    },
}})
