"""轮内技能发现，复用匹配器，只提供尚未展示的候选摘要。

模型可见：技能名称、简述与读取方式，不自动执行技能或注入全文。
Token 影响：候选与补充记忆共享有界预算，不重复提供已读技能。
缓存影响：通过回忆 provider 的动态区追加，稳定目录保持原样。
"""

from __future__ import annotations

from agent.memory.probe_render import RecallDelta
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import SkillState
from core.config import get_config_bool, get_config_int
from core.tags import tag_label


async def discover_skills(query: str, *, matcher: SkillMatcher, seen: set[str]) -> list[RecallDelta]:
    """只匹配与新执行线索相关的技能；候选不计为已读取或已完成。"""
    if not get_config_bool("skills_enabled", True):
        return []
    matches = await matcher.match([query], top_k=get_config_int("skills_match_top_k", 3))
    deltas = []
    for skill, _score in matches:
        identifier = f"skill:{skill.name}"
        if identifier in seen:
            continue
        description = skill.description.replace("\n", " ")[:200]
        archived = "已归档，确认适用时先 restore_skill；" if skill.state == SkillState.ARCHIVED else ""
        text = f"{tag_label('skill', skill.name)} {description}（{archived}需要使用时 get_skill 读取正文，候选不代表已执行）"
        deltas.append(RecallDelta("相关技能候选", text, identifier, text))
    return deltas
