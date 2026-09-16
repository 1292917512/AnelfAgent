"""用户话题指令（ban-topic）：显式"别再提 X"的抽取、TTL 与注入纪律。

语义挂在"说出来时的会话 scope"上——私聊里说的管住私聊，群里说的管住
那个群：不跨会话扩大化，也不做跨用户合并（A 在群里说的话题禁令不该
约束 B 的私聊）。沉默不续期：TTL 随命中次数线性增长（基准 3 天 × 命中
数，上限 30 天），到期自动失效——反复强调的禁令越来越长效，说一次的
自然淡忘，无需显式解禁指令。

链路三段：
- 抽取（observe_message）：``Mind.accept_feel`` 必经点调用，纯正则零
  LLM——指令句式语义明确，模型裁决反而引入误报与延迟；
- 注入（build_directives_block）：context 管线 discipline 块（稳定
  前缀区，内容仅指令增删时变化）；
- 清扫（sweep_expired）：心跳维护周期剔除过期项。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from core.config import (
    get_config_bool,
    get_config_int,
    register_configs_safe,
)
from core.log import log
from core.path import ConfigPaths

_DIRECTIVE_CONFIGS = {
    "memory/directives": {
        "memory_directives_enabled": {
            "description": "是否启用用户话题指令（「别再提X」自动抽取与注入）",
            "default": True,
        },
        "memory_directives_base_ttl_days": {
            "description": "指令基准有效期（天），实际 = 基准 × 命中次数",
            "default": 3,
            "advanced": True,
            "unit": "天",
        },
        "memory_directives_max_ttl_days": {
            "description": "指令有效期上限（反复强调也不再增长）",
            "default": 30,
            "advanced": True,
            "unit": "天",
        },
        "memory_directives_max_entries": {
            "description": "单会话指令容量上限（按最近命中淘汰）",
            "default": 40,
            "advanced": True,
            "unit": "条",
        },
    },
}

register_configs_safe(_DIRECTIVE_CONFIGS)

# 指令句式（zh/en 并行）：捕获组为话题词；术语长度 2-20，边界截断见 _clean_term。
# 只收明确禁令句式——"不想听音乐了"这类即时请求不是禁令，不收（防误伤）
_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"(?:别再提|不要再提|别提|不想再听|别再说|不要再聊|别再聊|少提)(?:关于|有关|起)?(.{2,20}?)(?=[，。！？；、,.!?;~\s]|$)"),
    re.compile(r"别跟我(?:说|聊)(?:起|到)?(?:关于|有关)?(.{2,20}?)(?=[，。！？；、,.!?;~\s]|$)"),
    re.compile(r"(?:stop|don't|dont|do\s+not)\s+(?:talking\s+about|mentioning|bringing\s+up)\s+(?:about\s+)?(.{2,40}?)(?=[.!?;,]|$)", re.IGNORECASE),
]

_TERM_MIN_CHARS = 2
_TERM_MAX_CHARS = 20

# 文件读-改-写串行锁：accept_feel（抽取）与心跳清扫可能并发
_store_lock = asyncio.Lock()


def _directives_path() -> Path:
    return Path(ConfigPaths.USER_DIRECTIVES)


def _load() -> Dict[str, List[Dict[str, Any]]]:
    p = _directives_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return data.get("scopes", {}) if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"话题指令加载失败: {exc}", "WARNING", tag="记忆")
        return {}


def _save(scopes: Dict[str, List[Dict[str, Any]]]) -> None:
    p = _directives_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"scopes": scopes}, ensure_ascii=False, indent=2), "utf-8",
        )
        tmp.replace(p)
    except Exception as exc:
        log(f"话题指令保存失败: {exc}", "WARNING", tag="记忆")


# 话题词首尾的虚词/标点字符集（逐字符剥离）
_TERM_TRIM_CHARS = set("的了着吧呢啊嘛呀哦!？?，。.,、~；;：: ")


def _clean_term(raw: str) -> str:
    """话题词清洗：剥首尾虚词/标点，长度不合规返回空。"""
    term = raw.strip()
    while term and term[0] in _TERM_TRIM_CHARS:
        term = term[1:]
    while term and term[-1] in _TERM_TRIM_CHARS:
        term = term[:-1]
    if len(term) < _TERM_MIN_CHARS or len(term) > _TERM_MAX_CHARS:
        return ""
    return term


def extract_directives(text: str) -> List[str]:
    """从一条用户消息抽取话题禁令词（可能多条；无命中返回空）。"""
    if not text:
        return []
    terms: List[str] = []
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            term = _clean_term(m.group(1))
            if term and term not in terms:
                terms.append(term)
    return terms


def _ttl_seconds(hit_count: int) -> int:
    base = max(1, get_config_int("memory_directives_base_ttl_days", 3))
    ceiling = max(base, get_config_int("memory_directives_max_ttl_days", 30))
    return min(base * max(1, hit_count), ceiling) * 86400


async def observe_message(scope: str, text: str) -> List[str]:
    """用户消息必经点：抽取指令词并登记/续期（返回本条新登记或续期的词）。"""
    if not scope or not get_config_bool("memory_directives_enabled", True):
        return []
    terms = extract_directives(text)
    if not terms:
        return []
    now = time.time()
    async with _store_lock:
        scopes = _load()
        entries = scopes.get(scope, [])
        by_term = {e["term"]: e for e in entries}
        changed = False
        for term in terms:
            entry = by_term.get(term)
            if entry is None:
                entries.append({
                    "term": term, "created_at": now,
                    "last_seen_at": now, "hit_count": 1,
                })
                changed = True
            else:
                entry["last_seen_at"] = now
                entry["hit_count"] = int(entry.get("hit_count", 1)) + 1
                changed = True
        if changed:
            cap = max(1, get_config_int("memory_directives_max_entries", 40))
            if len(entries) > cap:
                entries.sort(key=lambda e: e.get("last_seen_at", 0), reverse=True)
                entries[:] = entries[:cap]
            scopes[scope] = entries
            _save(scopes)
    log(f"话题指令登记 [{scope}]: {', '.join(terms)}", "INFO", tag="记忆")
    return terms


def active_terms(scope: str) -> List[str]:
    """会话当前生效的话题禁令词（按最近命中排序）。"""
    if not scope:
        return []
    now = time.time()
    entries = [
        e for e in _load().get(scope, [])
        if now - float(e.get("last_seen_at", 0)) <= _ttl_seconds(int(e.get("hit_count", 1)))
    ]
    entries.sort(key=lambda e: e.get("last_seen_at", 0), reverse=True)
    return [str(e["term"]) for e in entries]


def build_directives_block(scope: str) -> str:
    """渲染 discipline 注入块；无生效指令返回空串（块不注入）。"""
    terms = active_terms(scope)
    if not terms:
        return ""
    return (
        "[纪律·话题禁令] 对方明确要求过不要主动提起以下话题（除非对方先提起）：\n"
        + "、".join(terms)
        + "\n遵守这一边界是信任的一部分；到期未再强调会自动解除。"
    )


async def sweep_expired() -> int:
    """心跳维护：剔除全部过期指令（返回清理条数；无变化不写盘）。"""
    async with _store_lock:
        scopes = _load()
        now = time.time()
        removed = 0
        for key in list(scopes.keys()):
            kept = [
                e for e in scopes[key]
                if now - float(e.get("last_seen_at", 0))
                <= _ttl_seconds(int(e.get("hit_count", 1)))
            ]
            removed += len(scopes[key]) - len(kept)
            if len(kept) == len(scopes[key]):
                continue
            if kept:
                scopes[key] = kept
            else:
                scopes.pop(key)
        if removed:
            _save(scopes)
            log(f"话题指令清扫: 过期 {removed} 条", "DEBUG", tag="记忆")
    return removed
