"""技能自学习系统：任务完成 → 技能提取 → 存储 → 匹配 → 改进 → 策展。

三层职责（系统构建定位：事实归系统，决策归 AI）：
- skill_store:   物理层。SKILL.md 文件存储（YAML frontmatter + markdown），
                 use/match 信号分离，merge 原子落地
- skill_index:   事实层。向量/相似度/写入诊断/库健康快照/聚类——只产事实不做策略
- skill_matcher: 检索。关键词 + 语义混合匹配 + 近重复折叠（注入 volatile 层）
- background_review: 决策层。对话后后台评审，感知完备（语义候选 + 库健康），
                     沉淀/合并/治理由 LLM 自主决策
- curator:       重力（可逆衰减，含试用期快筛）+ 治理议程生成
- tools:         决策协议面。写入工具的诊断式响应 + decision 回执，
                 merge_skills / skill_library_health
"""

from agent.skills.background_review import SkillReviewer
from agent.skills.curator import SkillCurator
from agent.skills.skill_index import SkillIndex
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import Skill, SkillState, SkillStore

__all__ = [
    "Skill",
    "SkillCurator",
    "SkillIndex",
    "SkillMatcher",
    "SkillReviewer",
    "SkillState",
    "SkillStore",
]
