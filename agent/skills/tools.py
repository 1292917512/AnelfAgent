"""技能工具 — AI 可调用的技能管理接口。

通过 `register_skill_tools()` 在运行时注入依赖后批量注册到 EntityRegistry。
"""
from __future__ import annotations

import json
from typing import Optional

from core.log import log
from entities._sdk import activate_group, deferred_tool

from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import SkillStore

_store: Optional[SkillStore] = None
_matcher: Optional[SkillMatcher] = None


def register_skill_tools(store: SkillStore, matcher: SkillMatcher) -> None:
    """注入运行时依赖并批量注册技能工具。"""
    global _store, _matcher
    _store, _matcher = store, matcher
    count = activate_group("skills", "技能 - 经验技能的创建、检索、更新与管理")
    log(f"🎓 技能工具已注册 ({count} 个)", tag="技能")


def _not_ready() -> str:
    return json.dumps({"error": "技能系统未初始化"}, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="创建一个新技能：将可复用的方法、流程或知识保存下来，供以后遇到相似任务时参考。",
)
def create_skill(name: str, description: str, content: str, trigger_patterns: str = "") -> str:
    """创建新技能。

    Args:
        name: 技能名（英文短横线命名，如 web-research）
        description: 一句话描述技能用途
        content: 技能内容（markdown，步骤/要点/注意事项）
        trigger_patterns: 触发关键词，逗号分隔（遇到这些词时技能会被推荐）
    """
    if not _store:
        return _not_ready()
    patterns = [p.strip() for p in trigger_patterns.split(",") if p.strip()]
    skill = _store.create(
        name=name, description=description, content=content,
        trigger_patterns=patterns, created_by="agent",
    )
    return json.dumps({
        "ok": True, "name": skill.name,
        "message": f"技能 '{skill.name}' 已创建",
    }, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="更新已有技能：改进内容、补充触发词。增量更新，会记录 patch 次数。",
)
def update_skill(name: str, content: str = "", description: str = "", add_trigger_patterns: str = "") -> str:
    """更新技能。

    Args:
        name: 要更新的技能名
        content: 新的技能内容（完整替换旧内容，留空则不更新）
        description: 新的描述（留空则不更新）
        add_trigger_patterns: 追加的触发关键词，逗号分隔
    """
    if not _store:
        return _not_ready()
    patterns = [p.strip() for p in add_trigger_patterns.split(",") if p.strip()]
    skill = _store.patch(
        name,
        content=content or None,
        description=description or None,
        add_trigger_patterns=patterns or None,
    )
    if skill is None:
        return json.dumps({"error": f"技能 '{name}' 不存在"}, ensure_ascii=False)
    return json.dumps({
        "ok": True, "name": skill.name, "patch_count": skill.patch_count,
        "message": f"技能 '{skill.name}' 已更新（第 {skill.patch_count} 次修订）",
    }, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="搜索技能：按关键词和语义匹配已有技能，返回最相关的技能列表。",
)
async def search_skills(query: str, top_k: int = 5) -> str:
    """搜索技能。

    Args:
        query: 搜索关键词或描述
        top_k: 最多返回数量，默认 5
    """
    if not _store or not _matcher:
        return _not_ready()
    matched = await _matcher.match([query], top_k=max(1, min(top_k, 20)), min_score=0.0)
    results = [
        {
            "name": skill.name,
            "description": skill.description,
            "trigger_patterns": skill.trigger_patterns,
            "use_count": skill.use_count,
            "score": round(score, 3),
        }
        for skill, score in matched
    ]
    return json.dumps({"ok": True, "count": len(results), "skills": results}, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="列出全部技能（名称、描述、使用次数、状态）。",
)
def list_skills(include_archived: bool = False) -> str:
    """列出技能。

    Args:
        include_archived: 是否包含已归档的技能，默认否
    """
    if not _store:
        return _not_ready()
    skills = _store.list_skills(include_archived=include_archived)
    results = [
        {
            "name": s.name,
            "description": s.description,
            "state": s.state.value,
            "use_count": s.use_count,
            "patch_count": s.patch_count,
            "pinned": s.pinned,
        }
        for s in skills
    ]
    return json.dumps({"ok": True, "count": len(results), "skills": results}, ensure_ascii=False)


@deferred_tool(
    group="skills", tags=["always"], source="mind.skills",
    description="查看某个技能的完整内容。",
)
def get_skill(name: str) -> str:
    """查看技能详情。

    Args:
        name: 技能名
    """
    if not _store:
        return _not_ready()
    skill = _store.get(name)
    if skill is None:
        return json.dumps({"error": f"技能 '{name}' 不存在"}, ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "name": skill.name,
        "description": skill.description,
        "trigger_patterns": skill.trigger_patterns,
        "content": skill.content,
        "state": skill.state.value,
        "use_count": skill.use_count,
        "patch_count": skill.patch_count,
    }, ensure_ascii=False)
