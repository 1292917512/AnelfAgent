"""写入去重：候选召回 + 判断平台裁决（store / skip / update）。

规则仅拦截正文与完整标签均相同的重复；近似文本可能包含地址、数值、否定和条件
的修订，必须经语义裁决，不能按文字重叠率丢弃。本模块负责语义裁决：

1. 候选召回：FTS（jieba 词级）+ 向量并行召回，按融合名次限量；
2. 判断段（agent/judgment 平台，Jev 原生或普通模型回退）：一次调用并行评判——
   关系三选一（novel/covered/evolution）+ skip/update 目标选择，置信度不足保守退回直接写入；
3. 生成段：仅 update 需要合并文本时，经 light_llm 合成一次
   （store/skip 不调用生成模型，有候选时仍调用判断模型）；
4. 判断/合成不可用或无候选时回退为直接写入（去重永远不阻塞写入路径）。

memorize 工具与 auto_capture 自动提取管线共用本模块。

Model Experience：候选携带完整正文、身份与主题标签及记录时间，跨身份交判断模型裁决。
Token 影响：候选条数不变；完整正文增加内部判断输入，避免裁掉结尾的更正与条件。
缓存影响：内部判断与合成不改主对话稳定前缀。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from agent.judgment import (
    ChoiceAnswer,
    ChoiceQuestion,
    JudgmentError,
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
            reason = "不存在" if manager.get_client(model_id) is None else "已停用"
            log(
                f"轻量内部模型配置失效: memory_light_model='{model_id}'（{reason}）；"
                "本次调用改用默认主模型及其回退链。请在配置中心重新选择已启用模型，或清空此项跟随默认模型。",
                "WARNING",
                tag="思维",
            )
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
    """双路并行召回，按倒数名次融合限量；低向量分不排除 FTS 命中的事实演进。"""
    if not get_config_bool("memory_llm_dedup_enabled", True):
        return []
    limit: int = get_config_int("memory_dedup_candidate_limit", 8)
    if limit <= 0:
        return []
    vec_min: float = get_config_float("memory_dedup_vec_min_score", 0.45)

    async def text_lane() -> list[tuple[MemoryEntry, float]]:
        try:
            return await store.search_fts(content, limit=5)
        except Exception as exc:
            log(f"去重候选 FTS 召回失败: {exc}", "DEBUG", tag="记忆")
            return []

    async def vector_lane() -> list[tuple[MemoryEntry, float]]:
        if embedder is None:
            return []
        try:
            vec = await embedder.embed_query(content)
            if vec:
                return await store.search_vector(vec, limit=5, min_score=vec_min)
        except Exception as exc:
            log(f"去重候选向量召回失败: {exc}", "DEBUG", tag="记忆")
        return []

    merged: Dict[int, MemoryEntry] = {}
    ranks: dict[int, float] = {}
    for lane in await asyncio.gather(text_lane(), vector_lane()):
        seen: set[int] = set()
        for rank, (entry, _score) in enumerate(lane, start=1):
            if not entry.id or not _mergeable(entry) or entry.id in seen:
                continue
            seen.add(entry.id)
            previous = merged.get(entry.id)
            if previous is None or entry.version > previous.version:
                merged[entry.id] = entry
            ranks[entry.id] = ranks.get(entry.id, 0.0) + 1.0 / (60 + rank)
    ordered = sorted(merged, key=lambda key: (-ranks[key], -merged[key].timestamp, key))
    return [merged[key] for key in ordered[:limit]]


# ---------------------------------------------------------------------------
# 判断段（agent/judgment 平台）
# ---------------------------------------------------------------------------

# 关系三选一的选项语义（判断模型只看到选项名与描述，边界靠描述拉开）
_RELATION_CRITERIA: Dict[str, str] = {
    "novel": "与所有候选都不重复（不同事实或不同事件），应直接写入新记忆。"
    "同一句话涉及的 user:/group: 对象不同时，是不同人各自的事实，不是重复",
    "covered": "已被某条候选完整覆盖——同一事实且没有增加任何新信息"
    "（更简略的重述、子集表述也算覆盖），应放弃写入。"
    "不依赖说话人的通用事实在不同频道再次出现也算覆盖；覆盖不代表得到用户确认",
    "evolution": "是对某一条候选的更新、补充或修正（同一事实的新进展或更准确的表述），"
    "应合并进该候选，而不是新增一条造成矛盾或冗余",
}

_RELATION_TO_ACTION = {
    "novel": "store",
    "covered": "skip",
    "evolution": "update",
}


def _candidate_previews(candidates: List[MemoryEntry]) -> List[Dict[str, Any]]:
    """候选项的结构化快照（判断 state 与选项描述共用同一预览口径）。

    候选以 1..N 序号标识而非真实记忆 id——长数字 id 在判断模型转述时
    易被抄写错（抄错即目标校验失败退化为新增重复）；序号映射在裁决
    返回前还原为真实 id，判断模型只接触短序号。
    """
    previews: List[Dict[str, Any]] = []
    for seq, entry in enumerate(candidates, start=1):
        recorded_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.timestamp))
        previews.append({"seq": str(seq), "recorded_at": recorded_at, "content": entry.content, "tags": entry.tags})
    return previews


def _build_judgment_questions(previews: List[Dict[str, Any]]) -> Dict[str, Question]:
    """构造判断题面：关系三选一 + update 目标选择。"""
    questions: Dict[str, Question] = {
        "relation": ChoiceQuestion(
            instructions="根据完整正文和涉及对象判断关系，不能只看共同前缀。记录时间不是事实发生时间；"
            "数字、地点、否定或条件的变化属于新信息，不能当作已覆盖。",
            criteria=dict(_RELATION_CRITERIA),
        ),
        "target": ChoiceQuestion(
            instructions="若已有候选完整覆盖新记忆，或新记忆更新/补充/修正候选，应归属哪条候选？",
            criteria={
                **{p["seq"]: f"候选 {p['seq']}，以 state 中该序号的完整正文和标签为准" for p in previews},
                "none": "不属于对任何候选的更新",
            },
        ),
    }
    return questions


async def _synthesize_merged_content(content: str, targets: List[MemoryEntry]) -> str:
    """生成段：合成合并后的记忆文本（仅 update 需要；失败返回空串）。"""
    lines = [t.content for t in targets]
    prompt = (
        "你是记忆系统的文本合并器。把下列记忆合并为一条完整、准确的表述"
        "。保留未被本次变更影响的事实、条件、时间、来源与不确定性，不为缩短文本遗漏信息。"
        "修正应取代被否定的旧说法；记录时间不等于事件时间，不能据此编造事件日期。"
        "AI 自述不等于已验证事实。只输出更新后的事实正文，不添加处理过程、编号和记录时间。\n\n"
        f"【新记忆】\n{content}\n\n【待合并的既有记忆】\n" + "\n".join(lines)
    )
    try:
        return await light_llm(prompt)
    except Exception as exc:
        log(f"合并文本合成失败: {exc}", "DEBUG", tag="记忆")
        return ""


async def judge_write(
    content: str,
    candidates: List[MemoryEntry],
    *,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """对一条新记忆做写入裁决。无候选、判断失败或置信度不足时返回 store。

    判断段经 agent/judgment 平台（Jev 原生或普通模型回退）；仅 update 再经
    light_llm 合成合并文本。去重永远不阻塞写入路径。候选不做身份预过滤
    ——跨频道/跨人的相似内容由判断模型结合候选标签与新记忆标签裁决。
    多条互为片段的重复不做写入期合并（合并长文本稀释 embedding 语义），
    留给记忆整理任务按铁律 forget 收敛。
    """
    if not candidates or not get_config_bool("memory_llm_dedup_enabled", True):
        return {"action": "store"}

    previews = _candidate_previews(candidates)
    seq_to_id = {p["seq"]: e.id for p, e in zip(previews, candidates, strict=True) if e.id is not None}
    state: Dict[str, Any] = {
        "new_memory": content,
        "new_tags": list(tags or []),
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
            f"写入去重关系判断置信度不足（{relation.confidence:.2f} < {min_confidence}），保守直接写入",
            "DEBUG",
            tag="记忆",
        )
        return {"action": "store"}

    action = _RELATION_TO_ACTION.get(relation.choice, "store")
    log(
        f"写入去重裁决 [{report.source.value}]: {action} "
        f"(relation={relation.choice}, confidence={relation.confidence:.2f})",
        "DEBUG",
        tag="记忆",
    )
    if action == "store":
        return {"action": action}

    candidate_ids = {e.id for e in candidates if e.id is not None}
    if action in ("update", "skip"):
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
            log("写入去重目标无效或置信度不足，直接写入", "DEBUG", tag="记忆")
            return {"action": "store"}
        if action == "skip":
            return {"action": "skip", "target_id": target_id}
        target_entry = next(e for e in candidates if e.id == target_id)
        merged = await _synthesize_merged_content(content, [target_entry])
        if not merged:
            return {"action": "store"}
        return {
            "action": "update",
            "target_id": target_id,
            "expected_version": target_entry.version,
            "content": merged,
            "reason": f"同一事实的演进，合并进候选 #{target_id}",
        }
    # 未知 action 一律保守直接写入（与判断失败的降级语义一致）
    return {"action": "store"}


async def retain_duplicate_origin(
    store: MemoryStore,
    target_id: int,
    metadata: Dict[str, Any],
) -> bool:
    """为裁决选中的重复条目保留材料出处，不改变证据分或用户确认数。"""
    from .provenance import merge_metadata

    target = await store.get(target_id)
    if target is None:
        return False
    merged = merge_metadata(
        target.metadata, {key: metadata[key] for key in ("origins", "sensitivity") if key in metadata}
    )
    if merged == target.metadata:
        return True
    target.metadata = merged
    if not await store.update(target, actor="dedup_origin", expected_version=target.version):
        raise RuntimeError(f"重复记忆出处写入冲突 #{target_id}")
    return True


async def apply_update(
    store: MemoryStore,
    target_id: int,
    merged_content: str,
    extra_tags: Optional[List[str]] = None,
    *,
    actor: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    expected_version: int | None = None,
) -> Optional[MemoryEntry]:
    """应用更新，拒绝覆盖判断期间已改变的目标；调用方保留新事实并单独写入。"""
    target = await store.get(target_id)
    if target is None or (expected_version is not None and target.version != expected_version):
        return None
    merged_tags = list(dict.fromkeys(target.tags + extra_tags)) if extra_tags else target.tags
    from .provenance import merge_metadata

    merged_metadata = merge_metadata(target.metadata, metadata or {})
    if target.content == merged_content and merged_tags == target.tags and merged_metadata == target.metadata:
        # 内容与标签均无变化：无变化的 update 会空转审计并触发
        # cognee 投影重跑，是记忆写入风暴的主要来源之一
        return target
    target.content = merged_content
    target.tags = merged_tags
    target.metadata = merged_metadata
    # 正文变化时 Store 统一失效旧向量并唤醒后台回填
    ok = await store.update(target, actor=actor, expected_version=target.version)
    if not ok:
        return None
    return target


_DEDUP_CONFIGS = {
    "memory/dedup": {
        "memory_llm_dedup_enabled": {
            "description": "规则判重后是否再经语义裁决（store/skip/update）",
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
