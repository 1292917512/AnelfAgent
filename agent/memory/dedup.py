"""写入去重：候选召回 + 判断平台裁决（store / skip / update / merge）。

规则去重（子串/bigram）只能发现字面近重复；「我搬家了」对旧地址这类事实
演进检测不到，会累积互相矛盾的记录。本模块在规则去重之后加一道语义裁决：

1. 候选召回：FTS（jieba 词级）+ 向量双路取并集，上限 memory_dedup_candidate_limit；
2. 判断段（agent/judgment 平台，Jev 原生或普通模型回退）：一次调用并行评判——
   关系四选一（novel/covered/evolution/fragments）+ update 目标选择 +
   merge 逐候选是非，置信度不足保守退回直接写入；
3. 生成段：仅 update/merge 需要合并文本时，经 light_llm 合成一次
   （store/skip 占写入绝大多数，热路径零 LLM 调用）；
4. 判断/合成不可用或无候选时回退为直接写入（去重永远不阻塞写入路径）。

memorize 工具与 auto_capture 自动提取管线共用本模块。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agent.judgment import (
    ChoiceAnswer,
    ChoiceQuestion,
    JudgmentError,
    NoulAnswer,
    NoulQuestion,
    Question,
    get_judgment_engine,
)
from agent.llm.reasoning import CANONICAL_EFFORTS, normalize_effort
from core.config import (
    ConfigValueType,
    get_config,
    get_config_bool,
    get_config_float,
    get_config_int,
    register_configs_safe,
)
from core.log import log

from .memory_store import MemoryStore
from .memory_types import GOAL_SOURCE, MemoryEntry, MemoryType

# 判重候选准入排除项（系统独占维护的条目不参与语义合并）：
# - 画像由画像系统覆盖维护，permanent 走 upsert；
# - 规划条目（goal source）的 content 是结构化 JSON，合并会破坏结构
#   并让目标凭空消失（含 goal:{id} 标签的 memorize 极易命中）。
_EXCLUDED_TYPES = {MemoryType.ENTITY, MemoryType.PERMANENT}
_EXCLUDED_SOURCES = {GOAL_SOURCE}


def _mergeable(entry: MemoryEntry) -> bool:
    """判重候选准入：排除系统独占维护的条目（类型与来源双口径）。"""
    return entry.memory_type not in _EXCLUDED_TYPES and entry.source not in _EXCLUDED_SOURCES


async def light_llm(prompt: str, *, temperature: float = 0.1, timeout: float = 120.0) -> str:
    """轻量一次性 LLM 调用（无工具、带模型回退），供提取/合成类内部任务使用。

    经 chat_with_fallback 流式通道调用：按空闲窗口判死（思考/输出中不计时，
    完全静默超 timeout 才超时），长思考模型不再被墙钟掐断。可经
    memory_light_model 指定专用轻量模型（检索规划在回复关键路径上，更快
    的模型直接降低召回延迟；不存在/停用回落默认主模型），思考等级经
    memory_judge_reasoning_effort 配置（空 = 跟随模型自身配置）。

    Model Experience:
    - 模型看到什么：无 prompt 层变化；仅通道（流式）与可选专用模型/思考档位。
    - token 影响：指定低思考档可显著省 token；流式本身不改用量。
    - 缓存影响：独立小请求，不触碰任何对话前缀层。
    """
    from agent.llm import get_llm_manager

    manager = get_llm_manager()
    client = None
    model_id = str(get_config("memory_light_model", "") or "").strip()
    if model_id:
        client = manager.get_enabled_client(model_id)
        if client is None:
            log(f"轻量内部模型不存在或已停用，回落默认主模型: {model_id}",
                "WARNING", tag="思维")
    options: Dict[str, Any] = {"temperature": temperature}
    effort = normalize_effort(get_config("memory_judge_reasoning_effort", ""))
    if effort:
        options["reasoning_effort"] = effort
    result = await manager.chat_with_fallback(
        [{"role": "user", "content": prompt}],
        options=options,
        client=client,
        max_retries=1,
        timeout=timeout,
        stream=True,
    )
    return (getattr(result, "content", "") or "").strip()


async def gather_dedup_candidates(
    store: MemoryStore,
    embedder: Any,
    content: str,
) -> List[MemoryEntry]:
    """召回去重候选：FTS + 向量双路并集（按 id 去重，准入见 _mergeable）。"""
    limit: int = get_config_int("memory_dedup_candidate_limit", 8)
    vec_min: float = get_config_float("memory_dedup_vec_min_score", 0.45)

    merged: Dict[int, MemoryEntry] = {}
    try:
        for entry, _score in await store.search_fts(content, limit=5):
            if entry.id and _mergeable(entry):
                merged[entry.id] = entry
    except Exception as exc:
        log(f"去重候选 FTS 召回失败: {exc}", "DEBUG", tag="记忆")

    if embedder is not None:
        try:
            vec = await embedder.embed_query(content)
            if vec:
                for entry, _score in await store.search_vector(vec, limit=5, min_score=vec_min):
                    if entry.id and _mergeable(entry):
                        merged.setdefault(entry.id, entry)
        except Exception as exc:
            log(f"去重候选向量召回失败: {exc}", "DEBUG", tag="记忆")

    candidates = list(merged.values())[:limit]
    candidates.sort(key=lambda e: e.timestamp, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# 判断段（agent/judgment 平台）
# ---------------------------------------------------------------------------

# 关系四选一的选项语义（判断模型只看到选项名与描述，边界靠描述拉开）
_RELATION_CRITERIA: Dict[str, str] = {
    "novel": "与所有候选都不重复（不同事实或不同事件），应直接写入新记忆",
    "covered": "已被某条候选完整覆盖——同一事实且没有增加任何新信息"
               "（更简略的重述、子集表述也算覆盖），应放弃写入",
    "evolution": "是对某一条候选的更新、补充或修正（同一事实的新进展或更准确的表述），"
                 "应合并进该候选，而不是新增一条造成矛盾或冗余",
    "fragments": "与多条候选互为片段或重复，应把这些候选与新内容合并为一条完整记忆",
}

_RELATION_TO_ACTION = {
    "novel": "store",
    "covered": "skip",
    "evolution": "update",
    "fragments": "merge",
}


def _candidate_previews(candidates: List[MemoryEntry]) -> List[Dict[str, Any]]:
    """候选项的结构化快照（判断 state 与选项描述共用同一预览口径）。

    候选以 1..N 序号标识而非真实记忆 id——长数字 id 在判断模型转述时
    易被抄写错（抄错即目标校验失败退化为新增重复）；序号映射在裁决
    返回前还原为真实 id，判断模型只接触短序号。
    """
    previews: List[Dict[str, Any]] = []
    for seq, entry in enumerate(candidates, start=1):
        day = time.strftime("%m-%d", time.localtime(entry.timestamp))
        previews.append({"seq": str(seq), "date": day, "content": entry.content[:200]})
    return previews


def _build_judgment_questions(previews: List[Dict[str, Any]]) -> Dict[str, Question]:
    """构造判断题面：关系四选一 + update 目标选择 + merge 逐候选是非（投机题，按需消费）。"""
    questions: Dict[str, Question] = {
        "relation": ChoiceQuestion(
            instructions="这条新记忆与候选既有记忆是什么关系？据此决定新记忆的写入方式",
            criteria=dict(_RELATION_CRITERIA),
        ),
        "target": ChoiceQuestion(
            instructions="若新记忆是对某条候选的更新/补充/修正，应合并进哪条候选？",
            criteria={
                **{p["seq"]: f"候选 {p['seq']}（{p['date']}）{p['content'][:80]}"
                   for p in previews},
                "none": "不属于对任何候选的更新",
            },
        ),
    }
    for p in previews:
        questions[f"merge_fit_{p['seq']}"] = NoulQuestion(
            instructions=f"候选 {p['seq']}（{p['date']}）「{p['content'][:80]}」"
                         "是否与新记忆互为片段或重复，应与新内容合并为一条？"
        )
    return questions


async def _synthesize_merged_content(content: str, targets: List[MemoryEntry]) -> str:
    """生成段：合成合并后的记忆文本（仅 update/merge 需要；失败返回空串）。"""
    lines = [f"[#{t.id}]（{time.strftime('%m-%d', time.localtime(t.timestamp))}）{t.content}"
             for t in targets]
    prompt = (
        "你是记忆系统的文本合并器。把下列记忆合并为一条完整、准确的表述"
        "（一两句话，保留关键事实与时间信息）。只输出合并后的文本，不要输出任何其他内容。\n\n"
        f"【新记忆】\n{content}\n\n【待合并的既有记忆】\n" + "\n".join(lines)
    )
    try:
        return await light_llm(prompt)
    except Exception as exc:
        log(f"合并文本合成失败: {exc}", "DEBUG", tag="记忆")
        return ""


async def judge_write(content: str, candidates: List[MemoryEntry]) -> Dict[str, Any]:
    """对一条新记忆做写入裁决。无候选、判断失败或置信度不足时返回 store。

    判断段经 agent/judgment 平台（Jev 原生或普通模型回退）；仅 update/merge
    再经 light_llm 合成合并文本。去重永远不阻塞写入路径。
    """
    if not candidates or not get_config_bool("memory_llm_dedup_enabled", True):
        return {"action": "store"}

    previews = _candidate_previews(candidates)
    seq_to_id = {
        p["seq"]: e.id for p, e in zip(previews, candidates, strict=True)
        if e.id is not None
    }
    state: Dict[str, Any] = {
        "new_memory": content,
        "candidates": previews,
    }
    questions = _build_judgment_questions(previews)
    try:
        report = await get_judgment_engine().judge(state, questions)
    except JudgmentError as exc:
        log(f"写入去重判断失败（{exc.cause.value}），直接写入: {exc}", "DEBUG", tag="记忆")
        return {"action": "store"}
    except Exception as exc:  # 判断引擎之外的意外同样不阻塞写入
        log(f"写入去重判断异常，直接写入: {exc}", "DEBUG", tag="记忆")
        return {"action": "store"}

    relation = report.answers.get("relation")
    if not isinstance(relation, ChoiceAnswer):
        return {"action": "store"}
    min_confidence = get_config_float("memory_dedup_min_confidence", 0.25)
    if relation.confidence < min_confidence:
        log(
            f"写入去重关系判断置信度不足（{relation.confidence:.2f} < {min_confidence}），"
            "保守直接写入",
            "DEBUG", tag="记忆",
        )
        return {"action": "store"}

    action = _RELATION_TO_ACTION.get(relation.choice, "store")
    log(
        f"写入去重裁决 [{report.source.value}]: {action} "
        f"(relation={relation.choice}, confidence={relation.confidence:.2f})",
        "DEBUG", tag="记忆",
    )
    if action in ("store", "skip"):
        return {"action": action}

    candidate_ids = {e.id for e in candidates if e.id is not None}
    if action == "update":
        target_answer = report.answers.get("target")
        target_id = 0
        if isinstance(target_answer, ChoiceAnswer) and target_answer.choice != "none":
            target_id = seq_to_id.get(target_answer.choice, 0) or 0
        target_ok = (
            isinstance(target_answer, ChoiceAnswer)
            and target_id in candidate_ids
            and target_answer.confidence >= min_confidence
        )
        if not target_ok:
            log("写入去重 update 目标无效或置信度不足，直接写入", "DEBUG", tag="记忆")
            return {"action": "store"}
        target_entry = next(e for e in candidates if e.id == target_id)
        merged = await _synthesize_merged_content(content, [target_entry])
        if not merged:
            return {"action": "store"}
        return {
            "action": "update",
            "target_id": target_id,
            "content": merged,
            "reason": f"同一事实的演进，合并进候选 #{target_id}",
        }

    # merge：逐候选是非评判，越过 0.5 的进入合并集
    target_ids = []
    for p in previews:
        fit = report.answers.get(f"merge_fit_{p['seq']}")
        real_id = seq_to_id.get(p["seq"])
        if real_id is None:
            continue
        if isinstance(fit, NoulAnswer) and fit.noul > 0.5:
            target_ids.append(real_id)
    if not target_ids:
        log("写入去重 merge 无候选越界，直接写入", "DEBUG", tag="记忆")
        return {"action": "store"}
    targets = [e for e in candidates if e.id in target_ids]
    merged = await _synthesize_merged_content(content, targets)
    if not merged:
        return {"action": "store"}
    return {
        "action": "merge",
        "target_ids": target_ids,
        "content": merged,
        "reason": f"与 {len(target_ids)} 条候选互为片段，合并为一条",
    }


def _bigram_dice(a: str, b: str) -> float:
    """CJK 友好的 bigram Dice 相似度（信号归属用，候选内最相似者认领）。"""
    import re as _re
    ta = set(_re.sub(r"\s+", "", a or "")[i:i + 2] for i in range(max(0, len(_re.sub(r"\s+", "", a or "")) - 1)))
    tb = set(_re.sub(r"\s+", "", b or "")[i:i + 2] for i in range(max(0, len(_re.sub(r"\s+", "", b or "")) - 1)))
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


async def apply_evidence_signals(
    store: MemoryStore,
    action: str,
    new_content: str,
    candidates: List[MemoryEntry],
    *,
    target_ids: Optional[List[int]] = None,
) -> None:
    """裁决落地后的证据回流（fail-open，绝不阻塞写入路径）。

    用户对同一事实的复述/演进是既有记忆的确认信号（信号来自 dedup
    裁决这一硬判定，而非对话语气的猜测）：
    - skip（用户复述了既有事实）→ 候选中最相似者记一次用户确认（+1.0）；
    - update/merge（事实演进但谱系存活）→ target_ids 按 id 直取
      （merge 后旧 id 已归档，调用方须传合并产物的新 id），各记 +0.5。
    负向信号（反驳/纠错）不在此处判定——由反思生命周期（reflection_lifecycle）
    的自检与显式纠正路径施加，避免把"演进"误读为"被推翻"。
    """
    from .evidence import apply_reinforcement

    try:
        ids: List[int] = []
        delta = 0.5
        if action == "skip" and candidates:
            best = max(candidates, key=lambda e: _bigram_dice(e.content, new_content))
            if best.id is not None:
                ids = [best.id]
            delta = 1.0
        elif action in ("update", "merge") and target_ids:
            ids = [i for i in target_ids if i]
        for memory_id in ids:
            fresh = await store.get(memory_id)
            if fresh is None:
                continue
            apply_reinforcement(fresh.metadata, delta, user_originated=True)
            await store.update(fresh, actor="evidence_signal")
    except Exception as exc:
        log(f"证据信号回流失败（已忽略）: {exc}", "DEBUG", tag="记忆")


async def apply_update(
    store: MemoryStore,
    target_id: int,
    merged_content: str,
    extra_tags: Optional[List[str]] = None,
    *,
    actor: str = "",
) -> Optional[MemoryEntry]:
    """应用 update 裁决：合并内容写入目标记忆（version+1，向量待重建）。"""
    target = await store.get(target_id)
    if target is None:
        return None
    merged_tags = (
        list(dict.fromkeys(target.tags + extra_tags)) if extra_tags else target.tags
    )
    if target.content == merged_content and merged_tags == target.tags:
        # 内容与标签均无变化：无变化的 update 会空转审计并触发
        # cognee 投影重跑，是记忆写入风暴的主要来源之一
        return target
    target.content = merged_content
    target.tags = merged_tags
    target.embedding = None
    ok = await store.update(target, clear_embedding=True, actor=actor)
    if not ok:
        return None
    return target


_DEDUP_CONFIGS = {
    "memory/dedup": {
        "memory_llm_dedup_enabled": {
            "description": "规则判重后是否再经语义裁决（store/skip/update/merge）",
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
        "memory_dedup_min_confidence": {
            "description": "判重裁决的最低置信度（关系/目标判断的分布集中度低于此值时保守退回直接写入；0=不做置信度门槛）",
            "default": 0.25,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_light_model": {
            "description": "轻量内部调用的专用模型 ID（检索规划、合并文本合成、自动捕获提取等 light_llm 通道）：指定更快的已配置模型可显著降低回复关键路径上的规划延迟（不存在/停用回落默认主模型，失败仍走回退链）；空 = 默认主模型",
            "default": "",
            "value_type": ConfigValueType.MODEL,
            "advanced": True,
        },
        "memory_judge_reasoning_effort": {
            "description": "裁决/提取类内部调用的思考等级（检索规划、合并文本合成、自动捕获提取、关系抽取）：轻量任务通常无需深度思考，低档省时省 token（模型不支持思考时自动忽略）；空 = 跟随模型自身配置",
            "default": "",
            "value_type": ConfigValueType.ENUM,
            "options": ["", *CANONICAL_EFFORTS],
        },
    },
}

register_configs_safe(_DEDUP_CONFIGS)
