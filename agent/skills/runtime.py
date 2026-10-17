"""共享技能存储、匹配与维护依赖。"""

from __future__ import annotations

from dataclasses import dataclass

from agent.skills.curator import SkillCurator
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import SkillStore


@dataclass
class SkillRuntime:
    """技能系统的共享协作者。"""

    store: SkillStore
    matcher: SkillMatcher
    curator: SkillCurator
