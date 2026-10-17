"""技能匹配、显式调用解析与召回内容格式化。

模型可见：技能候选、显式请求的正文与关联 MCP 状态，沿用来源元数据。
Token：沿用查询车道、候选数量与正文预算，不新增模型调用。
缓存：仅生成召回区材料，不修改稳定工具目录。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol

from core.log import log

if TYPE_CHECKING:
    from agent.skills.skill_matcher import SkillMatcher
    from agent.skills.skill_store import SkillStore


class TemporaryNoticeSink(Protocol):
    """向指定会话记录技能请求未命中的提示。"""

    def __call__(self, message: dict, /, *, scope: str) -> None: ...


async def match_recalled_skills(
        store: SkillStore, matcher: SkillMatcher, gesture_names: list[str],
        add_temporary: TemporaryNoticeSink,
        base_query: str,
        *,
        query_vec: Optional[List[float]] = None,
        query_vec_task: asyncio.Future[list[float] | None] | None = None,
        scope: str = "",
        planned_queries: Optional[List[str]] = None,
) -> List[Dict]:
    """匹配当前对话相关技能，分别记录候选匹配与显式调用，返回注入消息。

    查询车道 = 对话尾部基查询（与记忆召回同源的 _extract_query 提取，
    query_vec 与之对应）+ 记忆召回规划产出的互补查询；向量读取复用 Embedder 缓存。

    gesture_names 由调用方从 SkillRuntime 按会话消费，本函数不持有请求队列。
    显式请求的技能绕过评分：闲置技能照常提供正文，已归档技能恢复后提供正文；
    关闭用户调用的技能跳过，不存在的名字经会话短期提示告知模型。
    """
    try:
        from agent.skills.skill_store import SkillState

        forced: List = []
        for name in gesture_names:
            skill = store.get(name)
            if skill is None:
                # 不存在：写短期记忆提示，模型可回应用户而非沉默忽略
                try:
                    add_temporary({
                        "role": "system",
                        "content": f"[系统] 用户请求的技能 /{name} 不存在或未启用，"
                                   f"可用技能可用 list_skills 查看。请向用户说明。",
                    }, scope=scope)
                except Exception:
                    pass
                log(f"技能手势未命中: /{name}（不存在）", "DEBUG", tag="技能")
                continue
            if not skill.user_invocable:
                log(f"技能 /{name} 已关闭用户手势调用（user_invocable=false）", "DEBUG", tag="技能")
                continue
            if skill.state is SkillState.ARCHIVED:
                skill = store.restore(name) or skill
                log(f"技能手势命中: /{name}（已归档，自动恢复）", tag="技能")
            else:
                log(f"技能手势命中: /{name}（确定性注入，跳过评分）", tag="技能")
            forced.append(skill)

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
        # 手势命中优先置顶（不与评分结果去重冲突：按名剔除重复）
        forced_names: set = set()
        if forced:
            forced_names = {s.name for s in forced}
            matched_skills = [(s, sc) for s, sc in matched_skills if s.name not in forced_names]
            matched_skills = [(s, 1.0) for s in forced] + matched_skills
        if not matched_skills:
            return []
        # 渐进披露：stable 工具块已含全部可用技能目录，此处只注入与当前任务
        # 相关度最高的少数技能作定向放大；正文经 get_skill 按需自取，
        # 手势命中（用户显式调用 /name）直接注入全文（超长截断）
        inject_max = get_config_int("skills_inject_max_chars", 8000)
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
            # 信号分离：手势命中 = 真实使用（计数 + 刷新活动）；评分命中 = 检索注入
            # （只计匹配，不刷新活动——被匹配不等于被消费，不能阻断闲置降级）
            if skill.name in forced_names:
                store.record_use(skill.name)
                from agent.skills.dependencies import dependency_notice
                notice = dependency_notice(skill)
                body = skill.content[:inject_max]
                if len(skill.content) > inject_max:
                    body += "\n\n（正文超长已截断，完整内容用 get_skill 查看）"
                entry_text = (
                    f"## {skill.name} — {skill.description}\n"
                    f"（用户显式调用，全文如下）\n{body}"
                )
                if notice:
                    entry_text += f"\n{notice}"
            else:
                store.record_match(skill.name)
                triggers = "、".join(skill.trigger_patterns[:6])
                desc = skill.description.replace("\n", " ")[:desc_max]
                if skill.state is SkillState.ARCHIVED:
                    archived_hit = True
                    entry = f"## {skill.name}【已归档】 — {desc}"
                else:
                    entry = f"## {skill.name} — {desc}"
                if triggers:
                    entry += f"\n触发词: {triggers}"
                entry_text = entry
            mcp_status = mcp_binding_status(skill, scope)
            mcp_notice = mcp_binding_notice(skill, scope, states=mcp_status)
            if mcp_notice:
                entry_text += f"\n{mcp_notice}"
            start = len("\n\n".join(skill_lines)) + 2
            skill_facts.append({
                "name": skill.name, "description": skill.description,
                "state": skill.state.value,
                "mcp_status": mcp_status,
                "mode": "explicit" if skill.name in forced_names else "candidate",
                "score": None if skill.name in forced_names else round(_score, 4),
                "heading_end": start + len(entry_text.split("\n", 1)[0]),
                "end": start + len(entry_text),
                "truncated": skill.name in forced_names and len(skill.content) > inject_max,
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
