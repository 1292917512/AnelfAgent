"""技能策展器 — 技能库的自动维护（参考 hermes-agent curator 确定性状态机）。

按最后活动时间自动迁移技能状态：
- active → stale:    超过 stale_after_days（默认 30 天）无活动
- stale → archived:  超过 archive_after_days（默认 90 天）无活动

规则：
- pinned 技能豁免一切自动迁移
- 只归档不删除（可恢复）
- 新技能以 created_at 锚定（刚创建不会立即被降级）

由心跳维护钩子周期性调用，无 LLM 参与，完全确定性。
"""
from __future__ import annotations

import time
from typing import Any, Dict

from agent.skills.skill_store import SkillState, SkillStore
from core.log import log

_DAY_SECONDS = 86400.0


class SkillCurator:
    """技能策展器：确定性状态迁移。"""

    def __init__(self, store: SkillStore) -> None:
        self._store = store

    @staticmethod
    def _config_days(key: str, default: int) -> float:
        from core.config import get_config_float
        return get_config_float(key, float(default)) * _DAY_SECONDS

    def apply_automatic_transitions(self, now: float | None = None) -> Dict[str, Any]:
        """执行自动状态迁移，返回迁移报告。"""
        now = now or time.time()
        stale_after = self._config_days("skills_stale_after_days", 30)
        archive_after = self._config_days("skills_archive_after_days", 90)

        report: Dict[str, Any] = {"staled": [], "archived": [], "skipped_pinned": 0}
        for skill in self._store.list_skills(include_archived=True):
            if skill.pinned:
                report["skipped_pinned"] += 1
                continue
            idle = now - skill.last_activity_at

            if skill.state == SkillState.ACTIVE and idle >= stale_after:
                self._store.set_state(skill.name, SkillState.STALE)
                report["staled"].append(skill.name)
            elif skill.state == SkillState.STALE and idle >= archive_after:
                self._store.set_state(skill.name, SkillState.ARCHIVED)
                report["archived"].append(skill.name)

        if report["staled"] or report["archived"]:
            log(
                f"技能策展: 降级 {len(report['staled'])} 个, 归档 {len(report['archived'])} 个",
                tag="技能",
            )
        return report


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_CURATOR_CONFIGS = {
    "技能": {
        "skills_enabled": {
            "description": "是否启用技能自学习系统",
            "default": True,
        },
        "skills_review_enabled": {
            "description": "是否启用对话后后台技能评审",
            "default": True,
        },
        "skills_match_top_k": {
            "description": "技能匹配注入的最大数量",
            "default": 3,
        },
        "skills_stale_after_days": {
            "description": "技能无活动降级为 stale 的天数",
            "default": 30,
        },
        "skills_archive_after_days": {
            "description": "技能无活动归档的天数",
            "default": 90,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_CURATOR_CONFIGS)
