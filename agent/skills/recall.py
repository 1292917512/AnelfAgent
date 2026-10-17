"""技能候选匹配与召回内容格式化。

模型可见：对话相关的技能摘要与关联 MCP 状态，正文由 AI 按需读取。
Token：有界候选摘要，不新增模型调用。
缓存：仅生成召回区材料，不修改稳定工具目录。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.log import log

if TYPE_CHECKING:
    from agent.skills.skill_matcher import SkillMatcher
    from agent.skills.skill_store import SkillStore


async def match_recalled_skills(
        store: SkillStore, matcher: SkillMatcher, base_query: str,
        *,
        query_vec: Optional[List[float]] = None,
        query_vec_task: asyncio.Future[list[float] | None] | None = None,
        scope: str = "",
        planned_queries: Optional[List[str]] = None,
) -> List[Dict]:
    """匹配对话与检索计划中的相关技能，记录候选事实并生成带来源的摘要。"""
    try:
        from agent.skills.skill_store import SkillState

        lanes = [base_query] if base_query.strip() else []
        for q in (planned_queries or []):
            q = q.strip()
            if q and q != base_query and q not in lanes:
                lanes.append(q)
            if len(lanes) >= 3:
                break
        from core.config import get_config_int
        top_k = get_config_int("skills_match_top_k", 3)
        matched_skills = await matcher.match(
            lanes, top_k=top_k, query_vec=query_vec,
            query_vec_task=query_vec_task,
        ) if lanes else []
        if not matched_skills:
            return []
        desc_max = get_config_int("skills_catalog_desc_chars", 160)
        skill_lines = [
            "[相关技能] 以下技能与当前任务相关度最高（全部可用技能见系统层技能库目录）。"
            "决定使用某个技能时，先用 get_skill 读取其完整内容再按步骤执行；"
            "不适用则忽略。",
        ]
        archived_hit = False
        skill_facts: list[dict[str, Any]] = []
        from agent.skills.dependencies import mcp_binding_notice, mcp_binding_status
        for matched_skill, _score in matched_skills:
            skill = store.get(matched_skill.name)
            if skill is None:
                continue
            store.record_match(skill.name)
            triggers = "、".join(skill.trigger_patterns[:6])
            desc = skill.description.replace("\n", " ")[:desc_max]
            if skill.state is SkillState.ARCHIVED:
                archived_hit = True
                entry_text = f"## {skill.name}【已归档】 — {desc}"
            else:
                entry_text = f"## {skill.name} — {desc}"
            if triggers:
                entry_text += f"\n触发词: {triggers}"
            mcp_status = mcp_binding_status(skill, scope)
            mcp_notice = mcp_binding_notice(skill, scope, states=mcp_status)
            if mcp_notice:
                entry_text += f"\n{mcp_notice}"
            start = len("\n\n".join(skill_lines)) + 2
            skill_facts.append({
                "name": skill.name, "description": skill.description,
                "state": skill.state.value,
                "mcp_status": mcp_status,
                "mode": "candidate",
                "score": round(_score, 4),
                "heading_end": start + len(entry_text.split("\n", 1)[0]),
                "end": start + len(entry_text),
                "truncated": False,
            })
            skill_lines.append(entry_text)
        if not skill_facts:
            return []
        if archived_hit:
            skill_lines.append(
                "已归档技能是历史沉淀的方法（长期未用移出目录，不代表失效）；"
                "确认适用时先 restore_skill 恢复，再用 get_skill 读取完整内容。"
            )
        log(f"技能注入: {', '.join(s.name for s, _ in matched_skills)}", "DEBUG", tag="技能")
        return [{
            "role": "system",
            "content": "\n\n".join(skill_lines),
            "_source": {"origin": "skill_match", "skills": skill_facts},
        }]
    except Exception as exc:
        log(f"技能匹配失败: {exc}", "DEBUG", tag="技能")
        return []
