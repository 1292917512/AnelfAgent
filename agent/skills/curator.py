"""技能策展器 — 技能库的自动维护（重力 + 议程）。

重力（确定性默认衰减，无 LLM，由心跳维护钩子周期执行）：
- active → stale:    超过 stale_after_days（默认 30 天）无真实活动
                      （真实活动 = 手势命中 / AI 读全文 / 更新；检索注入不算）
- stale → archived:  超过 archive_after_days（默认 90 天）无真实活动
                      **且**超过同窗口未被检索注入（仍被检索到的技能有保留价值）
- 试用期快筛：创建超过 probation_days（默认 14 天）零参与（无使用且无匹配）
                      的技能直接降级——沉淀失败的快速反馈

规则：
- pinned 技能豁免一切自动迁移
- 只归档不删除（可恢复；被合并归档的技能带 merged_into 追溯线索）
- 状态迁移不刷新活动时间（否则闲置计时永远被重置）

议程（build_agenda）：把库的健康事实整理成 AI 可消费的治理议程——
相似聚类 / 零参与 / 高匹配零消费 / 触发词碰撞 / 容量水位。议程只是事实，
治理决策（合并谁、归档谁、精简哪些词）归 AI（评审顺手治理或主动策展）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from agent.skills.skill_index import SkillIndex
from agent.skills.skill_store import SkillState, SkillStore
from core.log import log

_DAY_SECONDS = 86400.0


class SkillCurator:
    """技能策展器：确定性重力迁移 + 治理议程生成。"""

    def __init__(self, store: SkillStore, index: Optional[SkillIndex] = None) -> None:
        self._store = store
        self._index = index

    @staticmethod
    def _config_days(key: str, default: int) -> float:
        from core.config import get_config_float
        return get_config_float(key, float(default)) * _DAY_SECONDS

    def apply_automatic_transitions(self, now: float | None = None) -> Dict[str, Any]:
        """执行自动状态迁移（重力），返回迁移报告。"""
        now = now or time.time()
        stale_after = self._config_days("skills_stale_after_days", 30)
        archive_after = self._config_days("skills_archive_after_days", 90)
        probation_after = self._config_days("skills_probation_days", 14)

        report: Dict[str, Any] = {"staled": [], "archived": [], "skipped_pinned": 0}
        for skill in self._store.list_skills(include_archived=True):
            if skill.pinned:
                report["skipped_pinned"] += 1
                continue
            idle = now - skill.last_activity_at
            match_idle = now - skill.last_match_at

            if skill.state == SkillState.ACTIVE:
                # 试用期快筛：零参与（从未被真实用过，也从未被检索到）
                probation_idle = (
                    skill.use_count == 0 and skill.match_count == 0
                    and now - skill.created_at >= probation_after
                )
                if idle >= stale_after or probation_idle:
                    self._store.set_state(skill.name, SkillState.STALE)
                    report["staled"].append(skill.name)
            elif skill.state == SkillState.STALE:
                # 软保留：仍被检索注入的 stale 技能有召回价值，不归档
                if idle >= archive_after and match_idle >= archive_after:
                    self._store.set_state(skill.name, SkillState.ARCHIVED)
                    report["archived"].append(skill.name)

        if report["staled"] or report["archived"]:
            log(
                f"技能策展: 降级 {len(report['staled'])} 个, 归档 {len(report['archived'])} 个",
                tag="技能",
            )
        return report

    async def build_agenda(self) -> Dict[str, Any]:
        """生成治理议程：库健康事实 + 相似聚类（供心跳日志/AI 策展消费）。"""
        agenda: Dict[str, Any] = {}
        if self._index is None:
            return agenda
        try:
            agenda = dict(self._index.snapshot())
            clusters = await self._index.clusters()
            if clusters:
                agenda["merge_candidates"] = [
                    [{"name": s.name, "use_count": s.use_count} for s in cluster]
                    for cluster in clusters[:10]
                ]
        except Exception as exc:
            log(f"技能治理议程生成失败: {exc}", "DEBUG", tag="技能")
        return agenda

    async def warm_index(self, limit: int = 32) -> int:
        """批量预热技能向量（心跳维护周期调用，若干拍覆盖全库）。

        存在模型切换触发的待重建状态时，立即执行全量重建（一次性重建完），
        普通预热让位。
        """
        if self._index is None:
            return 0
        try:
            if self._index.rebuild_pending:
                return await self._index.rebuild_all()
            return await self._index.warm(limit=limit)
        except Exception as exc:
            log(f"技能向量预热失败: {exc}", "DEBUG", tag="技能")
            return 0

    @staticmethod
    def agenda_summary(agenda: Dict[str, Any]) -> str:
        """议程的一行式摘要（心跳日志用）。"""
        if not agenda:
            return ""
        counts = agenda.get("counts", {})
        parts = [f"库容 {counts.get('active', 0)}/{agenda.get('capacity_reference', '?')}"]
        if agenda.get("merge_candidates"):
            parts.append(f"合并候选 {len(agenda['merge_candidates'])} 组")
        if agenda.get("zero_engagement"):
            parts.append(f"零参与 {len(agenda['zero_engagement'])} 个")
        if agenda.get("trigger_collisions"):
            parts.append(f"触发词碰撞 {len(agenda['trigger_collisions'])} 个")
        return "；".join(parts)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_CURATOR_CONFIGS = {
    "skills/core": {
        "skills_enabled": {
            "description": "是否启用技能自学习系统",
            "default": True,
        },
        "skill_user_gesture_enabled": {
            "description": "启用 /技能名 用户手势：真实用户消息正文以 /技能名 开头时绕过语义评分"
                           "确定性加载该技能（防伪造：仅外部消息路径检测）",
            "default": True,
        },
        "skills_review_enabled": {
            "description": "是否启用对话后后台技能评审",
            "default": True,
        },
        "skills_match_top_k": {
            "description": "技能匹配注入的最大数量",
            "default": 3,
            "advanced": True,
            "unit": "个",
        },
        "skills_stale_after_days": {
            "description": "技能无真实活动降级为 stale 的天数（检索注入不算活动）",
            "default": 30,
            "advanced": True,
            "unit": "天",
        },
        "skills_archive_after_days": {
            "description": "技能无真实活动且无检索命中归档的天数",
            "default": 90,
            "advanced": True,
            "unit": "天",
        },
        "skills_probation_days": {
            "description": "新技能试用期：零参与（无使用且无匹配）直接降级的天数",
            "default": 14,
            "advanced": True,
            "unit": "天",
        },
        "skills_similar_threshold": {
            "description": "写入诊断的语义相近阈值：拟议技能与现有技能相似度超过该值时"
                           "返回决策请求（呈现事实由 AI 裁决，不拒绝写入）",
            "default": 0.83,
            "advanced": True,
        },
        "skills_match_redundancy": {
            "description": "检索注入的近重复折叠阈值：候选与已入选技能相似度超过该值时折叠"
                           "（保留得分更高者，折叠记入合并信号）",
            "default": 0.9,
            "advanced": True,
        },
        "skills_merge_similarity": {
            "description": "治理议程相似聚类的阈值（合并候选分组的依据）",
            "default": 0.8,
            "advanced": True,
        },
        "skills_capacity_reference": {
            "description": "技能库容量参考水位：active 超过该值作为事实呈现给评审与策展"
                           "（参考值而非上限，治理决策归 AI）",
            "default": 100,
            "advanced": True,
            "unit": "个",
        },
        "skills_trigger_collision_limit": {
            "description": "触发词碰撞呈现阈值：一个词被不少于该数量的技能持有时报告碰撞",
            "default": 3,
            "advanced": True,
            "unit": "个",
        },
        "skills_body_advise_chars": {
            "description": "技能正文建议长度：超过时写入诊断提示收敛（建议值而非上限）",
            "default": 2000,
            "advanced": True,
            "unit": "字",
        },
        "skills_embed_budget": {
            "description": "单次检索/诊断最多补算的技能向量数（冷缓存时其余走关键词路，"
                           "心跳批量预热逐步填满缓存）",
            "default": 16,
            "advanced": True,
            "unit": "个",
        },
        "skills_warm_batch_size": {
            "description": "心跳每拍预热的向量批量大小（渐进覆盖全库）",
            "default": 32,
            "advanced": True,
            "unit": "个",
        },
        "skills_rebuild_batch_size": {
            "description": "模型切换后全量重建的向量批量大小（一次性重建）",
            "default": 32,
            "advanced": True,
            "unit": "个",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_CURATOR_CONFIGS)
