"""技能目录 — 在役技能的全量紧凑清单，进 stable 工具块。

目录让模型每轮都能看到全部在役技能（名称 + 单行描述），技能寻址不依赖
匹配器命中——匹配注入只做"当前相关"的定向放大，不做可见性门控。

字节稳定（前缀缓存友好）：按入库时间排序，新技能只在尾部追加，既有行
不移动；库内容版本不变时目录文本字节不变（计数类账本更新不改变版本）。
预算超限时逐级降级（截断描述 → 仅名称 + 省略计数），任何库容下可注入。
"""
from __future__ import annotations

import threading
from typing import Dict, List, Tuple

from agent.skills.skill_store import Skill, SkillState, SkillStore

_HEADER = (
    "[技能库目录] 当前全部在役技能（按入库时间排序）。"
    "与当前任务相关时先用 get_skill 读取其完整内容再按步骤执行；"
    "不相关则忽略，不要逐条翻读。"
)

_cache_lock = threading.Lock()
# id(store) → (store 强引用, 库版本, 目录文本)：不同实例（测试/服务）互不串扰
_cache: Dict[int, Tuple[SkillStore, int, str]] = {}


def _catalog_enabled() -> bool:
    from core.config import get_config_bool
    return get_config_bool("skills_catalog_enabled", True)


def _line(skill: Skill, desc_chars: int) -> str:
    """单条目录行：技能名 + 单行描述（超长截断）。"""
    desc = skill.description.strip().replace("\n", " ")
    if len(desc) > desc_chars:
        desc = desc[:desc_chars - 1] + "…"
    return f"- {skill.name}: {desc}" if desc else f"- {skill.name}"


def _omission_line(count: int) -> str:
    return f"- …另有 {count} 个技能未列出（list_skills 查看全部）"


def _render(skills: List[Skill]) -> str:
    """渲染目录正文：预算内尽量保留描述，超限逐级降级。"""
    from core.config import get_config_int

    max_chars = get_config_int("skills_catalog_max_chars", 4000)
    desc_chars = get_config_int("skills_catalog_desc_chars", 72)

    def fits(lines: List[str]) -> bool:
        return len(_HEADER) + 1 + sum(len(line) + 1 for line in lines) <= max_chars

    ordered = sorted(skills, key=lambda s: (s.created_at, s.name))
    full = [_line(s, desc_chars) for s in ordered]
    if fits(full):
        return "\n".join(full)

    brief = [_line(s, max(12, desc_chars // 3)) for s in ordered]
    if fits(brief):
        return "\n".join(brief)

    names = [f"- {s.name}" for s in ordered]
    kept = len(names)
    while kept > 0 and not fits(names[:kept] + [_omission_line(len(names) - kept)]):
        kept -= 1
    return "\n".join(names[:kept] + [_omission_line(len(names) - kept)])


def catalog_section(store: SkillStore) -> str:
    """stable 工具块内的技能目录文本（未启用或空库时返回空串）。

    按库内容版本缓存：计数类更新不改变版本，目录字节保持稳定。
    """
    if not _catalog_enabled():
        return ""
    version = store.version
    with _cache_lock:
        entry = _cache.get(id(store))
        if entry and entry[0] is store and entry[1] == version:
            return entry[2]
    skills = [s for s in store.list_skills() if s.state == SkillState.ACTIVE]
    text = f"{_HEADER}\n{_render(skills)}" if skills else ""
    with _cache_lock:
        _cache[id(store)] = (store, version, text)
    return text


def catalog_factor(store: SkillStore) -> str:
    """目录指纹因子（启用位 + 库版本），供 stable 工具块指纹门控。"""
    return f"skills-catalog:{_catalog_enabled()}:{store.version}"


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

from core.config import register_configs_safe  # noqa: E402

register_configs_safe({"skills/catalog": {
    "skills_catalog_enabled": {
        "description": "在稳定上下文注入全量在役技能目录（技能寻址不依赖匹配命中）",
        "default": True,
    },
    "skills_catalog_max_chars": {
        "description": "技能目录注入的最大字符数（超限逐级降级：截断描述 → 仅名称）",
        "default": 4000,
        "advanced": True,
        "unit": "字符",
    },
    "skills_catalog_desc_chars": {
        "description": "技能目录单行描述的最大字符数",
        "default": 72,
        "advanced": True,
        "unit": "字符",
    },
    "skills_inject_max_chars": {
        "description": "手势命中注入正文的最大字符数（超限截断，全文经 get_skill 读取）",
        "default": 8000,
        "advanced": True,
        "unit": "字符",
    },
}})
