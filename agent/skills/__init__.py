"""技能自学习系统：任务完成 → 技能提取 → 存储 → 匹配 → 改进 → 策展。

- skill_store:   SKILL.md 文件存储（YAML frontmatter + markdown）
- skill_matcher: 关键词 + 语义混合匹配，注入 volatile 层
- background_review: 对话后后台评审，自动沉淀经验
- curator:       确定性状态机，自动降级/归档长期未用技能
- tools:         AI 可调用的技能管理工具
"""

from agent.skills.background_review import SkillReviewer
from agent.skills.curator import SkillCurator
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import Skill, SkillState, SkillStore
from agent.skills.tools import register_skill_tools

__all__ = [
    "Skill",
    "SkillCurator",
    "SkillMatcher",
    "SkillReviewer",
    "SkillState",
    "SkillStore",
    "register_skill_tools",
]
