"""技能目录 — 全部可用技能（active + stale）的紧凑清单，进 stable 工具块。

目录让模型每轮都能看到全部可用技能（名称 + 单行描述），技能寻址不依赖
匹配器命中——匹配注入只做"当前相关"的定向放大，不做可见性门控。
已归档技能不在目录（保持库容卫生），经匹配命中/搜索发现后可用
restore_skill 恢复回可用集。

字节稳定（前缀缓存友好）：按入库时间排序，新技能只在尾部追加，既有行
不移动；库内容版本不变时目录文本字节不变（计数类账本更新不改变版本，
可用技能的 active/stale 迁移不改变目录）。预算超限时逐级降级
（截断描述 → 仅名称 + 省略计数），任何库容下可注入。

Model Experience:
① 模型在 stable 工具块看到可用技能的名称与描述；实时状态通过技能工具查询。
② 省去每个闲置技能的状态后缀，节省少量 token，不缩减寻址信息。
③ 活跃与闲置迁移不破坏前缀；归档、增删和描述修订仍如实更新目录。
"""
from __future__ import annotations

import threading
from typing import Dict, List, Tuple

from agent.skills.skill_store import Skill, SkillStore

_HEADER = (
    "[技能库目录] 全部可用技能（含活跃与闲置，按入库时间排序；状态可用 list_skills 查询）。"
    "与当前任务相关时先用 get_skill 读取完整内容再按步骤执行，不相关则忽略、"
    "不要逐条翻读；已归档技能不在此列，"
    "匹配命中后可用 restore_skill 恢复。"
)

_cache_lock = threading.Lock()
# id(store) → (store 强引用, 内容版本与预算, 目录文本)
_cache: Dict[int, Tuple[SkillStore, tuple[int, int, int], str]] = {}


def _catalog_enabled() -> bool:
    from core.config import get_config_bool
    return get_config_bool("skills_catalog_enabled", True)


def _line(skill: Skill, desc_chars: int) -> str:
    """单条目录行：技能名 + 单行描述（超长截断）。"""
    desc = skill.description.strip().replace("\n", " ")
    if desc and desc_chars > 0:
        if len(desc) > desc_chars:
            desc = desc[:desc_chars - 1] + "…"
        text = f"- {skill.name}: {desc}"
    else:
        text = f"- {skill.name}"
    return text


def _omission_line(count: int) -> str:
    return f"- …另有 {count} 个技能未列出（list_skills 查看全部）"


def _catalog_budget() -> tuple[int, int]:
    from core.config import get_config_int
    return get_config_int("skills_catalog_max_chars", 16000), get_config_int("skills_catalog_desc_chars", 160)


def _render(skills: List[Skill], max_chars: int, desc_chars: int) -> str:
    """渲染目录正文：预算内尽量保留描述，超限逐级降级。"""

    def fits(lines: List[str]) -> bool:
        return len(_HEADER) + 1 + sum(len(line) + 1 for line in lines) <= max_chars

    ordered = sorted(skills, key=lambda s: (s.created_at, s.name))
    full = [_line(s, desc_chars) for s in ordered]
    if fits(full):
        return "\n".join(full)

    brief = [_line(s, max(48, desc_chars // 2)) for s in ordered]
    if fits(brief):
        return "\n".join(brief)

    names = [_line(s, 0) for s in ordered]
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
    max_chars, desc_chars = _catalog_budget()
    key = (store.version, max_chars, desc_chars)
    with _cache_lock:
        entry = _cache.get(id(store))
        if entry and entry[0] is store and entry[1] == key:
            return entry[2]
    # list_skills 默认排除归档：目录 = 全部可用技能（active + stale）
    skills = store.list_skills()
    text = f"{_HEADER}\n{_render(skills, max_chars, desc_chars)}" if skills else ""
    with _cache_lock:
        _cache[id(store)] = (store, key, text)
    return text


def catalog_factor(store: SkillStore) -> str:
    """目录渲染所依赖的启用位、库版本和长度预算。"""
    max_chars, desc_chars = _catalog_budget()
    return f"skills-catalog:{_catalog_enabled()}:{store.version}:{max_chars}:{desc_chars}"


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

from core.config import register_configs_safe  # noqa: E402

register_configs_safe({"skills/catalog": {
    "skills_catalog_enabled": {
        "description": "在稳定上下文注入全部可用技能目录（技能寻址不依赖匹配命中）",
        "default": True,
    },
    "skills_catalog_max_chars": {
        "description": "技能目录注入的最大字符数（超限逐级降级：截断描述 → 仅名称）",
        "default": 16000,
        "advanced": True,
        "unit": "字符",
    },
    "skills_catalog_desc_chars": {
        "description": "技能目录单行描述的最大字符数（描述是模型判断相关性的路由依据，"
                       "过短会导致技能存在但被忽略）",
        "default": 160,
        "advanced": True,
        "unit": "字符",
    },
}})
