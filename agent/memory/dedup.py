"""LLM 判定式写入去重：候选召回 + 单次 LLM 裁决（store / skip / update）。

规则去重（子串/bigram）只能发现字面近重复；「我搬家了」对旧地址这类事实
演进检测不到，会累积互相矛盾的记录。本模块在规则去重之后加一道语义裁决：

1. 候选召回：FTS（jieba 词级）+ 向量双路取并集，上限 memory_dedup_candidate_limit；
2. 单次 LLM 批量裁决：store（无重复直接写）/ skip（已被覆盖）/
   update（事实演进，合并进指定候选并使其 version+1）；
3. LLM 不可用或无候选时回退为直接写入（去重永远不阻塞写入路径）。

memorize 工具与 auto_capture 自动提取管线共用本模块。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from core.config import get_config_bool, get_config_float, get_config_int, register_configs_safe
from core.log import log

from .memory_store import MemoryStore
from .memory_types import MemoryEntry, MemoryType

# 画像/永久记忆不参与去重裁决：画像由画像系统覆盖维护，permanent 走 upsert
_EXCLUDED_TYPES = {MemoryType.ENTITY, MemoryType.PERMANENT}

_DEDUP_JUDGE_PROMPT = """\
你是记忆系统的写入裁决器。一条新记忆即将写入长期记忆库，下面是与它可能相关的既有记忆。
请判定这条新记忆应如何处理：

- store：与所有候选都不重复（不同事实），直接写入
- skip：已被某条候选完整覆盖（同一事实且无新信息），放弃写入
- update：是对某条候选的更新/补充/修正（同一事实的新进展或更准确的表述），
  应合并进该候选，而不是新增一条造成两条矛盾或冗余的记录
- merge：与多条候选互为片段/重复，应把它们与新内容合并为一条完整记忆

只输出 JSON（不要输出任何其他内容）：
{{"action": "store" 或 "skip" 或 "update" 或 "merge",
  "target_id": 候选编号（action 为 update 时必填）,
  "target_ids": [候选编号列表]（action 为 merge 时必填，至少 1 个）,
  "content": "合并后的完整记忆内容，一两句话（action 为 update/merge 时必填）",
  "reason": "一句话理由"}}

【新记忆】
{content}

【候选既有记忆】
{candidates}
"""

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


async def light_llm(prompt: str, *, temperature: float = 0.1, timeout: float = 30.0) -> str:
    """轻量一次性 LLM 调用（无工具、带模型回退），供裁决/提取类内部任务使用。"""
    from agent.llm import get_llm_manager

    result = await get_llm_manager().chat_with_fallback(
        [{"role": "user", "content": prompt}],
        options={"temperature": temperature},
        max_retries=1,
        timeout=timeout,
    )
    return (getattr(result, "content", "") or "").strip()


async def gather_dedup_candidates(
    store: MemoryStore,
    embedder: Any,
    content: str,
) -> List[MemoryEntry]:
    """召回去重候选：FTS + 向量双路并集（按 id 去重，过滤画像/永久记忆）。"""
    limit: int = get_config_int("memory_dedup_candidate_limit", 8)
    vec_min: float = get_config_float("memory_dedup_vec_min_score", 0.45)

    merged: Dict[int, MemoryEntry] = {}
    try:
        for entry, _score in await store.search_fts(content, limit=5):
            if entry.id and entry.memory_type not in _EXCLUDED_TYPES:
                merged[entry.id] = entry
    except Exception as exc:
        log(f"去重候选 FTS 召回失败: {exc}", "DEBUG", tag="记忆")

    if embedder is not None:
        try:
            vec = await embedder.embed_query(content)
            if vec:
                for entry, _score in await store.search_vector(vec, limit=5, min_score=vec_min):
                    if entry.id and entry.memory_type not in _EXCLUDED_TYPES:
                        merged.setdefault(entry.id, entry)
        except Exception as exc:
            log(f"去重候选向量召回失败: {exc}", "DEBUG", tag="记忆")

    candidates = list(merged.values())[:limit]
    candidates.sort(key=lambda e: e.timestamp, reverse=True)
    return candidates


def parse_judgement(raw: str, candidate_ids: set[int]) -> Optional[Dict[str, Any]]:
    """解析 LLM 裁决输出（容错：提取首个 JSON 对象 + 字段校验）。

    update 动作必须命中有效候选且给出非空合并内容，否则降级为 store。
    """
    m = _JSON_OBJ_RE.search(raw or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        # 常见修复：单引号 / 尾逗号
        try:
            data = json.loads(re.sub(r",\s*}", "}", m.group(0)).replace("'", '"'))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "")).strip().lower()
    if action not in ("store", "skip", "update", "merge"):
        return None
    if action in ("update", "merge"):
        content = str(data.get("content", "")).strip()
        if action == "update":
            try:
                target_id = int(data.get("target_id") or 0)
            except (TypeError, ValueError):
                target_id = 0
            if target_id not in candidate_ids or not content:
                action = "store"
                data["target_id"] = None
            else:
                data["target_id"] = target_id
                data["content"] = content
        else:  # merge：target_ids 至少命中一个有效候选
            raw_ids = data.get("target_ids") or []
            if not isinstance(raw_ids, list):
                raw_ids = [raw_ids]
            target_ids: list[int] = []
            for v in raw_ids:
                try:
                    vid = int(v)
                except (TypeError, ValueError):
                    continue
                if vid in candidate_ids and vid not in target_ids:
                    target_ids.append(vid)
            if not target_ids or not content:
                action = "store"
                data["target_ids"] = []
            else:
                data["target_ids"] = target_ids
                data["content"] = content
    data["action"] = action
    return data


async def judge_write(content: str, candidates: List[MemoryEntry]) -> Dict[str, Any]:
    """对一条新记忆做写入裁决。无候选或 LLM 失败时返回 store。"""
    if not candidates or not get_config_bool("memory_llm_dedup_enabled", True):
        return {"action": "store"}

    lines = []
    for entry in candidates:
        day = time.strftime("%m-%d", time.localtime(entry.timestamp))
        lines.append(f"[#{entry.id}]（{day}）{entry.content[:200]}")
    prompt = _DEDUP_JUDGE_PROMPT.replace("{content}", content).replace(
        "{candidates}", "\n".join(lines)
    )
    try:
        raw = await light_llm(prompt)
    except Exception as exc:
        log(f"写入去重 LLM 裁决失败，直接写入: {exc}", "DEBUG", tag="记忆")
        return {"action": "store"}

    decision = parse_judgement(raw, {e.id for e in candidates if e.id is not None})
    if decision is None:
        log(f"写入去重裁决输出解析失败，直接写入: {raw[:100]}", "DEBUG", tag="记忆")
        return {"action": "store"}
    return decision


async def apply_update(
    store: MemoryStore,
    target_id: int,
    merged_content: str,
    extra_tags: Optional[List[str]] = None,
) -> Optional[MemoryEntry]:
    """应用 update 裁决：合并内容写入目标记忆（version+1，向量待重建）。"""
    target = await store.get(target_id)
    if target is None:
        return None
    target.content = merged_content
    if extra_tags:
        target.tags = list(dict.fromkeys(target.tags + extra_tags))
    target.embedding = None
    ok = await store.update(target, clear_embedding=True)
    if not ok:
        return None
    return target


_DEDUP_CONFIGS = {
    "memory/dedup": {
        "memory_llm_dedup_enabled": {
            "description": "规则判重后是否再经 LLM 裁决（store/skip/update）",
            "default": True,
        },
        "memory_dedup_candidate_limit": {
            "description": "判重裁决的最大候选条数",
            "default": 8,
            "advanced": True,
            "unit": "条",
        },
        "memory_dedup_vec_min_score": {
            "description": "向量候选的最低相似度",
            "default": 0.45,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
    },
}

register_configs_safe(_DEDUP_CONFIGS)
