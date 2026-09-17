"""音频核心 AI 工具面：声纹身份管理、声纹识别、转写检索与编辑、库统计。

设计原则（AI 视角）：
- speaker 参数统一接受 数字id / speaker_key / 姓名（内部模糊解析，重名返回候选供追问）
- 时间参数接受自然日期串（'2026-08-01' / '2026-08-01 14:00' / epoch 秒）
- 查询类工具常驻（tags=always）；管理/音频类按需激活（core / media:voice）
- 返回统一 JSON 字符串；错误用 tool_error/error_from_exception 归因
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.config import ConfigManager, get_config_bool
from core.log import log
from entities._sdk import (
    KIND_ASR,
    ErrorCause,
    audio_has_provider,
    audio_transcribe,
    deferred_tool,
    error_from_exception,
    tool_error,
)

from . import matcher
from .store import get_audio_store, parse_time_ns
from .vectors import cosine, sample_weight, unit_rows, weighted_centroid

_group = "audio"
_LOG_TAG = "音频"


def _gate() -> Optional[str]:
    """核心开关门控：audio_ai_enabled 关闭时拒绝 AI 调用。"""
    if not get_config_bool("audio_ai_enabled", True):
        return tool_error(
            "音频核心能力已停用", cause=ErrorCause.STATE, retryable=False,
            hint="请在音频页签配置中开启 audio_ai_enabled")
    return None


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


async def _resolve_speaker(ref: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """解析说话人引用（id / speaker_key / 姓名）。返回 (speaker, error_json)。"""
    ref = (ref or "").strip()
    if not ref:
        return None, tool_error("speaker 参数不能为空", cause=ErrorCause.PARAM, retryable=False)
    store = get_audio_store()
    matches = await store.find_speakers(ref)
    if not matches:
        return None, tool_error(
            f"未找到说话人: {ref}", cause=ErrorCause.NOT_FOUND, retryable=False,
            hint="可用 speaker_list 查看全部说话人")
    exact = [m for m in matches if m["name"] == ref or m["speaker_key"] == ref
             or str(m["id"]) == ref]
    if len(matches) > 1 and len(exact) != 1:
        candidates = [
            {"id": m["id"], "speaker_key": m["speaker_key"], "name": m["name"],
             "role": m["role"], "status": m["status"]}
            for m in matches[:5]
        ]
        return None, tool_error(
            f"'{ref}' 匹配到多个说话人，请用 id 或 speaker_key 指定",
            cause=ErrorCause.PARAM, retryable=False, candidates=candidates)
    return (exact[0] if exact else matches[0]), ""


def _resolve_audio_path(path: str) -> str:
    """解析音频路径：绝对路径直用，相对路径走 workspace 沙箱解析。"""
    path = (path or "").strip()
    if not path:
        raise ValueError("audio_path 不能为空")
    if os.path.isabs(path):
        return path
    from agent.approval.policy import workspace_paths_port
    if workspace_paths_port.bound:
        return workspace_paths_port.get().resolve(path)
    raise ValueError("相对路径需要 workspace 解析（请传绝对路径）")


# ------------------------------------------------------------------
# 查询类（常驻）
# ------------------------------------------------------------------


@deferred_tool(group=_group, tags=["always"], concurrency_safe=True)
async def speaker_list(status: str = "", keyword: str = "", limit: int = 20) -> str:
    """列出音频库中的说话人（含样本数/累计时长/实体绑定/最近出现），支持状态与关键字过滤。

    Args:
        status: 状态过滤，'confirmed'（已确认）或 'pending'（待确认），空为全部
        keyword: 姓名/角色/编号模糊过滤，空为全部
        limit: 返回数量上限（默认 20）
    """
    if (gate := _gate()):
        return gate
    try:
        store = get_audio_store()
        result = await store.list_speakers(status=status, keyword=keyword, limit=limit)
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="列出说话人")


@deferred_tool(group=_group, tags=["always"], concurrency_safe=True)
async def speaker_get(speaker: str) -> str:
    """查看说话人档案详情：样本池、有效阈值、实体绑定、统计信息与近期话语。

    Args:
        speaker: 说话人引用（数字id / speaker_key / 姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_speaker(speaker)
        if err:
            return err
        assert target is not None
        store = get_audio_store()
        samples = await store.list_samples(int(target["id"]))
        recent = await store.list_segments(speaker_id=int(target["id"]), limit=5)
        return _dump({
            "speaker": target,
            "effective_threshold": matcher.effective_threshold(target),
            "samples": samples,
            "recent_segments": recent["items"],
        })
    except Exception as e:
        return error_from_exception(e, action=f"查看说话人 [{speaker}]")


@deferred_tool(group=_group, tags=["always"], concurrency_safe=True)
async def transcript_search(
    query: str = "",
    speaker: str = "",
    entity: str = "",
    time_from: str = "",
    time_to: str = "",
    limit: int = 10,
) -> str:
    """检索语音转写记录：语义+全文混合搜索特定话语内容，可按说话人和时间段过滤。

    Args:
        query: 检索内容（自然语言），空则纯时间线查询
        speaker: 说话人引用（id/key/姓名），空为全部人
        entity: 实体 scope（如 'user:qq:456'，按声纹绑定过滤该实体说过的话），空不限
        time_from: 起始时间（如 '2026-08-01' 或 '2026-08-01 14:00'），空不限
        time_to: 结束时间，空不限
        limit: 返回数量上限（默认 10）
    """
    if (gate := _gate()):
        return gate
    try:
        speaker_id: Optional[int] = None
        if speaker.strip():
            target, err = await _resolve_speaker(speaker)
            if err:
                return err
            assert target is not None
            speaker_id = int(target["id"])
        entity_scope = entity.strip()
        if entity_scope and not entity_scope.startswith(("user:", "group:", "agent:")):
            return tool_error(
                "entity 须为实体 scope（user:/group:/agent: 前缀，见音频库声纹关联清单）",
                cause=ErrorCause.PARAM, retryable=False)

        from_ns = parse_time_ns(time_from)
        to_ns = parse_time_ns(time_to)
        if time_from.strip() and from_ns is None:
            return tool_error(f"无法解析 time_from: {time_from}", cause=ErrorCause.PARAM,
                              retryable=False, hint="格式如 '2026-08-01' 或 '2026-08-01 14:00'")
        if time_to.strip() and to_ns is None:
            return tool_error(f"无法解析 time_to: {time_to}", cause=ErrorCause.PARAM,
                              retryable=False, hint="格式如 '2026-08-01' 或 '2026-08-01 14:00'")

        query_vec: Optional[List[float]] = None
        if query.strip():
            query_vec = await _embed_query(query)

        store = get_audio_store()
        items = await store.search_segments(
            query, query_vec=query_vec, speaker_id=speaker_id,
            entity_scope=entity_scope,
            from_ns=from_ns, to_ns=to_ns, limit=max(1, min(limit, 50)))
        return _dump({"items": items, "total": len(items), "query": query})
    except Exception as e:
        return error_from_exception(e, action=f"检索转写 [{query}]")


@deferred_tool(group=_group, tags=["always"], concurrency_safe=True)
async def speaker_segments(
    speaker: str,
    time_from: str = "",
    time_to: str = "",
    limit: int = 20,
) -> str:
    """查看指定说话人的话语时间线（按时间倒序）。

    Args:
        speaker: 说话人引用（id/key/姓名）
        time_from: 起始时间（如 '2026-08-01'），空不限
        time_to: 结束时间，空不限
        limit: 返回数量上限（默认 20）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_speaker(speaker)
        if err:
            return err
        assert target is not None
        store = get_audio_store()
        result = await store.list_segments(
            speaker_id=int(target["id"]),
            from_ns=parse_time_ns(time_from),
            to_ns=parse_time_ns(time_to),
            limit=max(1, min(limit, 100)))
        result["speaker"] = {"id": target["id"], "name": target["name"],
                             "speaker_key": target["speaker_key"]}
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action=f"查看话语记录 [{speaker}]")


@deferred_tool(group=_group, tags=["always"], concurrency_safe=True)
async def audio_stats() -> str:
    """音频库总览：说话人数/待确认数/实体绑定数/样本数/片段数/未读数/索引状态。"""
    if (gate := _gate()):
        return gate
    try:
        store = get_audio_store()
        stats = await store.stats()
        stats["match_threshold"] = matcher.global_threshold()
        stats["asr_configured"] = await audio_has_provider(KIND_ASR)
        stats["text_embedding_model"] = str(
            ConfigManager.get("embedding_text_model", "") or "") or "default"
        return _dump(stats)
    except Exception as e:
        return error_from_exception(e, action="查看音频库统计")


# ------------------------------------------------------------------
# 管理类（按需激活）
# ------------------------------------------------------------------


@deferred_tool(group=_group, tags=["core"])
async def speaker_update(
    speaker: str,
    name: str = "",
    role: str = "",
    notes: str = "",
    status: str = "",
    threshold: float = -1.0,
) -> str:
    """编辑说话人归属：命名/角色/备注/确认状态/独立识别阈值（仅更新传入的字段）。

    Args:
        speaker: 说话人引用（id/key/姓名）
        name: 新姓名（同时用于确认临时说话人），空不改
        role: 自定义角色标签（如 '家人'/'同事'），空不改
        notes: 自定义备注，空不改
        status: 'confirmed' 确认 / 'pending' 待确认，空不改
        threshold: 该说话人的独立匹配阈值（0~1，覆盖全局），负值不改
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_speaker(speaker)
        if err:
            return err
        assert target is not None
        if status and status not in ("confirmed", "pending"):
            return tool_error("status 仅支持 confirmed/pending", cause=ErrorCause.PARAM,
                              retryable=False)
        fields: Dict[str, Any] = {}
        if name:
            fields["name"] = name
        if role:
            fields["role"] = role
        if notes:
            fields["notes"] = notes
        if status:
            fields["status"] = status
        if 0.0 <= threshold <= 1.0:
            fields["threshold"] = threshold
        if not fields:
            return tool_error("没有需要更新的字段", cause=ErrorCause.PARAM, retryable=False,
                              hint="至少传入 name/role/notes/status/threshold 之一")
        store = get_audio_store()
        updated = await store.update_speaker(int(target["id"]), **fields)
        return _dump({"speaker": updated, "updated_fields": sorted(fields)})
    except Exception as e:
        return error_from_exception(e, action=f"编辑说话人 [{speaker}]")


@deferred_tool(group=_group, tags=["core"])
async def speaker_bind(speaker: str, entity_scope: str = "") -> str:
    """声纹身份 ↔ 实体画像绑定：绑定后检索到该说话人的话语即知对应哪个实体（用户/群/自身）。

    反向可用：实体画像侧经 speakers_for_entity 查询其绑定的全部声纹身份。

    Args:
        speaker: 说话人引用（id/key/姓名）
        entity_scope: 实体 scope（如 'user:webui:123' / 'group:qq:456' / 'agent:self'），
            空串解除绑定
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_speaker(speaker)
        if err:
            return err
        assert target is not None
        store = get_audio_store()
        try:
            updated = await store.bind_entity(int(target["id"]), entity_scope)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
        return _dump({"speaker": updated,
                      "bound": bool(entity_scope.strip()),
                      "entity_scope": entity_scope.strip()})
    except Exception as e:
        return error_from_exception(e, action=f"绑定实体 [{speaker}]")


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True)
async def speaker_compare(speaker_a: str, speaker_b: str) -> str:
    """精确对比两个说话人的声纹：锚对锚（长期身份）、样本最佳配对、同信道模板交叉。

    合并/绑定前的终极核查——返回各判据分值与相对合并阈值的判读。

    Args:
        speaker_a: 说话人引用（id/key/姓名）
        speaker_b: 说话人引用（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target_a, err = await _resolve_speaker(speaker_a)
        if err:
            return err
        target_b, err = await _resolve_speaker(speaker_b)
        if err:
            return err
        assert target_a is not None and target_b is not None
        result = await matcher.compare(
            get_audio_store(), int(target_a["id"]), int(target_b["id"]))
        return _dump(result)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"对比说话人 [{speaker_a} vs {speaker_b}]")


@deferred_tool(group=_group, tags=["core", "media:voice", "media:audio"])
async def voice_compare(audio_a: str, audio_b: str) -> str:
    """对比两段音频的声纹是否同一人：各自提取声纹后给质心与最佳段配对相似度。

    Args:
        audio_a: 第一段音频路径（绝对路径或 workspace 相对路径）
        audio_b: 第二段音频路径
    """
    if (gate := _gate()):
        return gate
    try:
        if not await audio_has_provider(KIND_ASR):
            return tool_error(
                "无可用 ASR 提供者，无法提取声纹", cause=ErrorCause.CONFIG,
                retryable=False, hint="请在音源同步组件配置 FunASR 服务地址")

        async def _voiced(audio_path: str) -> List[Tuple[List[float], int]]:
            resolved = _resolve_audio_path(audio_path)
            if not os.path.isfile(resolved):
                raise FileNotFoundError(audio_path)
            segments = await audio_transcribe(resolved)
            return [
                (s["vector"], max(0, int(s["end_ms"]) - int(s["start_ms"])))
                for s in segments if s.get("vector")]

        voiced_a = await _voiced(audio_a)
        voiced_b = await _voiced(audio_b)
        if not voiced_a or not voiced_b:
            return tool_error("音频中未提取到有效声纹", cause=ErrorCause.STATE,
                              retryable=True, hint="换一段包含清晰人声的音频")
        centroid_a = weighted_centroid([(v, sample_weight(d)) for v, d in voiced_a])
        centroid_b = weighted_centroid([(v, sample_weight(d)) for v, d in voiced_b])
        assert centroid_a is not None and centroid_b is not None
        matrix_a = np.asarray([v for v, _ in voiced_a])
        matrix_b = np.asarray([v for v, _ in voiced_b])
        best_pair = float((unit_rows(matrix_a) @ unit_rows(matrix_b).T).max())
        overall = cosine(centroid_a[0], centroid_b[0])
        threshold = matcher.global_threshold()
        verdict = "很可能是同一人" if max(overall, best_pair) >= threshold else "相似度不足，倾向不同人"
        return _dump({
            "centroid_similarity": round(overall, 4),
            "best_segment_similarity": round(best_pair, 4),
            "segments": [len(voiced_a), len(voiced_b)],
            "threshold": threshold,
            "hint": f"质心 vs 最佳段配对（{round(max(overall, best_pair), 4)} / 阈值 {threshold}）：{verdict}",
        })
    except FileNotFoundError as e:
        return tool_error(f"音频文件不存在: {e}", cause=ErrorCause.NOT_FOUND, retryable=False)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"对比音频 [{audio_a} vs {audio_b}]")


@deferred_tool(group=_group, tags=["core"])
async def speaker_merge(source: str, target: str) -> str:
    """合并两个说话人身份：source 的样本池/话语记录/统计并入 target，source 删除。

    声纹锚按累计权重精确合成（与重放两档案全部历史样本等价）——
    合并是"同一人被分裂成两个档案"的归一路径。

    Args:
        source: 被合并的说话人（通常是临时ID，id/key/姓名）
        target: 保留的目标说话人（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        src, err = await _resolve_speaker(source)
        if err:
            return err
        dst, err = await _resolve_speaker(target)
        if err:
            return err
        assert src is not None and dst is not None
        store = get_audio_store()
        result = await matcher.merge(store, int(src["id"]), int(dst["id"]))
        return _dump(result)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"合并说话人 [{source}→{target}]")


@deferred_tool(group=_group, tags=["core"])
async def speaker_refine(speaker: str) -> str:
    """重建声纹锚：以当前样本池重立（剔除坏样本后用它复位，丢弃被污染的历史累积）。

    声纹锚（历史合格样本的加权质心）随每次合格采样自动折叠更新，
    常规情况无需重建；适用场景是手动剔除坏样本后复位、或怀疑锚被
    长期误匹配带偏。返回漂移（新旧锚余弦，越接近 1 变化越小）。

    Args:
        speaker: 说话人引用（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_speaker(speaker)
        if err:
            return err
        assert target is not None
        result = await matcher.refine(get_audio_store(), int(target["id"]))
        return _dump(result)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"重建声纹 [{speaker}]")


@deferred_tool(group=_group, tags=["core"])
async def speaker_delete(speaker: str) -> str:
    """删除说话人记录（级联：声纹样本与全部话语片段一并删除）。

    注意：删除即整体移除相关内容。想保留话语只改归属请用 speaker_merge 合并
    或对片段逐条改派。

    Args:
        speaker: 说话人引用（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_speaker(speaker)
        if err:
            return err
        assert target is not None
        store = get_audio_store()
        deleted = await store.delete_speaker(int(target["id"]))
        return _dump({"deleted": deleted})
    except Exception as e:
        return error_from_exception(e, action=f"删除说话人 [{speaker}]")


@deferred_tool(group=_group, tags=["core", "media:voice", "media:audio"])
async def speaker_enroll(
    name: str,
    audio_path: str = "",
    role: str = "",
    notes: str = "",
    entity_scope: str = "",
) -> str:
    """注册说话人：提供音频提取声纹建档（冷启动/已知人录入）。

    同名已确认档案直接累积样本（一人一档案）；声音对不上的累积
    会被相干门拒入（返回 sample_rejected 标记）。

    Args:
        name: 说话人姓名
        audio_path: 该说话人的清晰音频文件路径（经 ASR 服务提取声纹）
        role: 自定义角色标签（如 '家人'）
        notes: 备注
        entity_scope: 同时绑定实体 scope（如 'user:webui:123'），空不绑定
    """
    if (gate := _gate()):
        return gate
    try:
        if not audio_path.strip():
            return tool_error(
                "请提供 audio_path（AI 无法直接产生声纹向量）",
                cause=ErrorCause.PARAM, retryable=False,
                hint="Web 面板支持向量直传注册；或先配置 ASR 转写服务")
        if not await audio_has_provider(KIND_ASR):
            return tool_error(
                "无可用 ASR 提供者，无法从音频提取声纹",
                cause=ErrorCause.CONFIG, retryable=False,
                hint="请在音源同步组件配置 FunASR 服务地址")
        resolved = _resolve_audio_path(audio_path)
        if not os.path.isfile(resolved):
            return tool_error(f"音频文件不存在: {audio_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)
        segments = await audio_transcribe(resolved)
        voiced = [(s["vector"], max(0, int(s["end_ms"]) - int(s["start_ms"])))
                  for s in segments if s.get("vector")]
        if not voiced:
            return tool_error("音频中未提取到有效声纹", cause=ErrorCause.STATE,
                              retryable=True, hint="换一段包含清晰人声的音频")
        result = await matcher.enroll_samples(
            get_audio_store(), name, voiced, role=role, notes=notes,
            device_source=resolved, entity_scope=entity_scope.strip())
        if result["sample_rejected"]:
            result["hint"] = "部分样本与既有声纹相干度过低被拒入，请确认音频属于本人"
        return _dump(result)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"注册说话人 [{name}]")


@deferred_tool(group=_group, tags=["core", "media:voice", "media:audio"])
async def voice_identify(audio_path: str, ingest: bool = False) -> str:
    """识别音频中的说话人：转写+提声纹，逐段匹配返回 Top 候选与新人标记。

    Args:
        audio_path: 音频文件路径（绝对路径或 workspace 相对路径）
        ingest: 识别结果是否同时入库（True 时片段进入音频库可被检索）
    """
    if (gate := _gate()):
        return gate
    try:
        if not await audio_has_provider(KIND_ASR):
            return tool_error(
                "无可用 ASR 提供者", cause=ErrorCause.CONFIG, retryable=False,
                hint="请在音源同步组件配置 FunASR 服务地址")
        resolved = _resolve_audio_path(audio_path)
        if not os.path.isfile(resolved):
            return tool_error(f"音频文件不存在: {audio_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)
        segments = await audio_transcribe(resolved)
        if not segments:
            return _dump({"segments": [], "message": "音频中未检测到有效语音段"})

        store = get_audio_store()
        from .schemas import IngestPayload, SegmentIn
        if ingest:
            from .ingest import ingest_payload
            ingest_result = await ingest_payload(IngestPayload(
                source_file=resolved,
                segments=[SegmentIn(
                    start_ms=s["start_ms"], end_ms=s["end_ms"],
                    text=s["text"], vector=s["vector"],
                    abs_start_ms=s.get("abs_start_ms"),
                    abs_end_ms=s.get("abs_end_ms"),
                ) for s in segments],
            ), store=store)
            return _dump({"ingested": True, **ingest_result.model_dump()})

        # 仅识别不入库
        items = []
        for seg in segments:
            candidates = await matcher.match_vector(store, seg["vector"]) if seg.get("vector") else []
            items.append({
                "start_ms": seg["start_ms"],
                "end_ms": seg["end_ms"],
                "text": seg["text"],
                "best_match": candidates[0] if candidates else None,
                "candidates": candidates,
            })
        return _dump({"ingested": False, "segments": items})
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"识别音频 [{audio_path}]")


@deferred_tool(group=_group, tags=["core"])
async def speaker_profile(speaker: str) -> str:
    """导出说话人结构化画像文本（可配合 memorize 工具沉淀到长期记忆）。

    Args:
        speaker: 说话人引用（id/key/姓名）
    """
    if (gate := _gate()):
        return gate
    try:
        target, err = await _resolve_speaker(speaker)
        if err:
            return err
        assert target is not None
        store = get_audio_store()
        samples = await store.list_samples(int(target["id"]))
        hours = round(target["total_audio_ms"] / 3_600_000, 2)
        lines = [
            f"说话人画像 [{target['speaker_key']}]",
            f"姓名: {target['name'] or '（未命名）'}",
            f"角色: {target['role'] or '（未设置）'}",
            f"状态: {'已确认' if target['status'] == 'confirmed' else '待确认'}",
            f"累计有效音频: {hours} 小时",
            f"命中次数: {target['match_count']}",
            f"声纹样本数: {len(samples)}",
        ]
        if target["entity_scope"]:
            lines.append(f"绑定实体: {target['entity_scope']}")
        if target["device_source"]:
            lines.append(f"常见设备来源: {target['device_source']}")
        if target["notes"]:
            lines.append(f"备注: {target['notes']}")
        return _dump({
            "speaker_id": target["id"],
            "profile_text": "\n".join(lines),
            "hint": "可将 profile_text 用 memorize 工具沉淀到长期记忆",
        })
    except Exception as e:
        return error_from_exception(e, action=f"导出画像 [{speaker}]")


@deferred_tool(group=_group, tags=["core"])
async def audio_set_threshold(value: float) -> str:
    """调整全局声纹匹配阈值（默认 0.75；提高更严格减少误判，降低更宽松减少漏识）。

    Args:
        value: 新阈值（0~1，建议 0.7~0.85）
    """
    if (gate := _gate()):
        return gate
    if not 0.0 < value < 1.0:
        return tool_error("阈值须在 0~1 之间", cause=ErrorCause.PARAM, retryable=False,
                          hint="建议范围 0.7~0.85，默认 0.75")
    try:
        ConfigManager.set("audio_match_threshold", round(value, 4))
        return _dump({"match_threshold": round(value, 4)})
    except Exception as e:
        return error_from_exception(e, action="调整匹配阈值")


@deferred_tool(group=_group, tags=["core"])
async def audio_mark_read(segment_ids: str = "", read: bool = True) -> str:
    """批量标记语音片段已读/未读（清空未读收件箱、恢复未读或指定片段）。

    Args:
        segment_ids: 逗号分隔的片段 id，空为全部
        read: true 标记已读 / false 恢复未读
    """
    if (gate := _gate()):
        return gate
    try:
        ids: Optional[List[int]] = None
        if segment_ids.strip():
            try:
                ids = [int(x.strip()) for x in segment_ids.split(",") if x.strip()]
            except ValueError:
                return tool_error("segment_ids 须为逗号分隔的数字", cause=ErrorCause.PARAM,
                                  retryable=False)
        store = get_audio_store()
        marked = await store.mark_read(ids, read=read)
        return _dump({"marked": marked, "read": read})
    except Exception as e:
        return error_from_exception(e, action="标记已读状态")


@deferred_tool(group=_group, tags=["core"])
async def transcript_replace(
    find: str,
    replace: str,
    speaker: str = "",
    time_from: str = "",
    time_to: str = "",
    dry_run: bool = False,
) -> str:
    """批量查找替换转写文本：人名/术语纠错一次全改（可限定说话人和时间段）。

    先用 dry_run=true 预览影响条数与样本，确认无误后再正式执行。
    修正后文本向量自动重建（语义检索即刻按新文本生效）。

    Args:
        find: 被替换的错误文本（如误识别的人名）
        replace: 替换为的正确文本
        speaker: 限定说话人（id/key/姓名），空为全部
        time_from: 起始时间（如 '2026-08-01'），空不限
        time_to: 结束时间，空不限
        dry_run: true 时只预览不写入
    """
    if (gate := _gate()):
        return gate
    try:
        if not find.strip():
            return tool_error("find 不能为空", cause=ErrorCause.PARAM, retryable=False)
        speaker_id: Optional[int] = None
        if speaker.strip():
            target, err = await _resolve_speaker(speaker)
            if err:
                return err
            assert target is not None
            speaker_id = int(target["id"])
        store = get_audio_store()
        result = await store.replace_in_transcripts(
            find, replace,
            speaker_id=speaker_id,
            from_ns=parse_time_ns(time_from),
            to_ns=parse_time_ns(time_to),
            dry_run=dry_run)
        result["dry_run"] = dry_run
        if dry_run and result["matched"]:
            result["hint"] = "确认无误后用 dry_run=false 再执行一次正式替换"
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action=f"批量替换 [{find}→{replace}]")


@deferred_tool(group=_group, tags=["core"])
async def transcript_update(updates: str) -> str:
    """批量修订转写文本：传入 JSON 数组，逐条按片段 id 更新文本（AI 读一批改一批）。

    Args:
        updates: JSON 数组字符串，如 '[{"id": 12, "text": "修正后的文本"}, {"id": 13, "text": "..."}]'
    """
    if (gate := _gate()):
        return gate
    try:
        try:
            items = json.loads(updates)
        except json.JSONDecodeError as e:
            return tool_error(f"updates 不是合法 JSON: {e}", cause=ErrorCause.PARAM,
                              retryable=False,
                              hint='格式如 [{"id": 12, "text": "修正后的文本"}]')
        if not isinstance(items, list) or not items:
            return tool_error("updates 须为非空 JSON 数组", cause=ErrorCause.PARAM,
                              retryable=False)
        store = get_audio_store()
        updated_ids: List[int] = []
        errors: List[Dict[str, Any]] = []
        for item in items[:200]:
            if not isinstance(item, dict) or "id" not in item or "text" not in item:
                errors.append({"item": item, "error": "缺少 id 或 text 字段"})
                continue
            updated = await store.update_transcript(int(item["id"]), str(item["text"]))
            if updated:
                updated_ids.append(int(item["id"]))
            else:
                errors.append({"id": item["id"], "error": "片段不存在"})
        result: Dict[str, Any] = {"updated": len(updated_ids), "ids": updated_ids}
        if errors:
            result["errors"] = errors
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="批量修订转写")


@deferred_tool(group=_group, tags=["core"])
async def transcript_merge(
    ids: str,
    text: str = "",
    speaker: str = "",
) -> str:
    """合并多个相邻语音片段为一条：转写碎片归并（一句话被切成多段时用）。

    保留首条：文本按序拼接（或自定义）、时间跨度取首~尾、归属取首条（或指定），
    其余片段删除；语义检索向量自动重建。限同一录制单元内的片段。

    Args:
        ids: 逗号分隔的片段 id（≥2 个，按时间顺序给出，如 '12,13,14'）
        text: 自定义合并后的文本，空则按序拼接各段文本
        speaker: 指定归属说话人（id/key/姓名），空则取首条归属
    """
    if (gate := _gate()):
        return gate
    try:
        try:
            id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
        except ValueError:
            return tool_error("ids 须为逗号分隔的数字", cause=ErrorCause.PARAM,
                              retryable=False)
        if len(id_list) < 2:
            return tool_error("合并至少需要 2 个片段 id", cause=ErrorCause.PARAM,
                              retryable=False)
        speaker_id: Optional[int] = None
        if speaker.strip():
            target, err = await _resolve_speaker(speaker)
            if err:
                return err
            assert target is not None
            speaker_id = int(target["id"])
        store = get_audio_store()
        try:
            merged = await store.merge_segments(
                id_list,
                transcript=text.strip() or None,
                speaker_id=speaker_id)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
        if not merged:
            return tool_error("片段不存在", cause=ErrorCause.NOT_FOUND, retryable=False)
        return _dump({"segment": merged, "merged_count": len(id_list)})
    except Exception as e:
        return error_from_exception(e, action=f"合并片段 [{ids}]")


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True)
async def speaker_similarity_map(
    status: str = "pending",
    neighbors: int = 3,
    threshold: float = 0.0,
) -> str:
    """声纹相似度分布图：按相契度聚邻排序的全景数据（合并决策的核心依据）。

    返回：估计真实人数（簇数）+ 每个说话人的 top-N 最相似他人（mergable 标记）
    + 分簇结果。相契的说话人排在一起——先扫 estimated_persons 建立人数预期，
    再按簇逐个确认/合并，比逐个判断快得多。

    Args:
        status: 范围（'pending' 待确认 / 'confirmed' 已确认 / 空为全部）
        neighbors: 每人返回的最相似他人数量（默认 3）
        threshold: 相契判定阈值（0 用全局 audio_merge_threshold，默认 0.70）
    """
    if (gate := _gate()):
        return gate
    try:
        from .consolidate import similarity_map
        store = get_audio_store()
        result = await similarity_map(
            store, status=status, neighbors=neighbors,
            threshold=threshold if 0.0 < threshold < 1.0 else None)
        result.pop("matrix", None)  # 矩阵供 Web 热力图，AI 用聚邻列表即可
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="相似度分布图")


@deferred_tool(group=_group, tags=["core"], concurrency_safe=True)
async def segment_context(segment_id: int) -> str:
    """片段上下文联想：归属/相似说话人/同录制全部片段/前后时间段对话。

    合并或改派前先看这个：声纹相契度 + 对话上下文结合，判断"这句话到底是谁说的"。

    Args:
        segment_id: 片段 id
    """
    if (gate := _gate()):
        return gate
    try:
        store = get_audio_store()
        segment = await store.get_segment(segment_id)
        if not segment:
            return tool_error(f"片段不存在: {segment_id}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)
        result: Dict[str, Any] = {"segment": segment}

        # 归属说话人 + 其最相似他人（合并候选）
        if segment["speaker_id"]:
            speaker = await store.get_speaker(int(segment["speaker_id"]))
            if speaker:
                result["speaker"] = speaker
                from .consolidate import similarity_map
                smap = await similarity_map(store, status="", neighbors=3)
                for entry in smap["speakers"]:
                    if entry["id"] == speaker["id"]:
                        result["similar_speakers"] = entry["top_similar"]
                        break

        # 同录制全部片段（对话流程）
        siblings = await store.list_segments(
            recording_path=segment["recording_path"], limit=50, order="asc")
        result["recording_segments"] = siblings["items"]

        # 前后 10 分钟对话上下文（可能跨录制/说话人）
        ts = int(segment["ts_ns"])
        neighbors = await store.list_segments(
            from_ns=ts - 600 * 1_000_000_000,
            to_ns=ts + 600 * 1_000_000_000,
            limit=20, order="asc")
        result["time_neighbors"] = [
            s for s in neighbors["items"] if s["id"] != segment_id]
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action=f"片段上下文 [{segment_id}]")


@deferred_tool(group=_group, tags=["core"])
async def transcript_split(
    segment_id: int,
    at_ms: int,
    text_first: str = "",
    text_second: str = "",
    speaker_second: str = "",
) -> str:
    """把片段在指定时间点拆为两段：一段含多人话语或边界切错时手动拆段。

    首段保留原 id（end=at_ms），次段继承时间/录制/批偏移（ts 按切点顺延），
    可分别指定文本与次段归属；两者语义向量自动重建。配合 voice_listen 回听
    确定切点与各自内容后使用最准。

    Args:
        segment_id: 片段 id
        at_ms: 切点（批内毫秒，须在片段 start_ms 与 end_ms 之间）
        text_first: 首段文本，空则保留原文本
        text_second: 次段文本，空则保留原文本
        speaker_second: 次段归属（id/key/姓名），空则继承原归属；传 'unknown' 置为未知
    """
    if (gate := _gate()):
        return gate
    try:
        speaker_second_id: Optional[int] = None
        speaker_second_set = False
        if speaker_second.strip():
            speaker_second_set = True
            if speaker_second.strip().lower() != "unknown":
                target, err = await _resolve_speaker(speaker_second)
                if err:
                    return err
                assert target is not None
                speaker_second_id = int(target["id"])
        store = get_audio_store()
        try:
            result = await store.split_segment(
                segment_id, at_ms,
                text_first=text_first.strip() or None,
                text_second=text_second.strip() or None,
                speaker_second_id=speaker_second_id,
                speaker_second_set=speaker_second_set)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
        if not result:
            return tool_error(f"片段不存在: {segment_id}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action=f"拆段 [{segment_id}@{at_ms}ms]")


@deferred_tool(group=_group, tags=["core"])
async def segment_add(
    text: str,
    speaker: str = "",
    time: str = "",
    recording_path: str = "",
    start_ms: int = 0,
    end_ms: int = 0,
    part_start_ms: int = 0,
) -> str:
    """手动新增语音段落：补充转写遗漏或记录回听到的新内容（指定时间点）。

    Args:
        text: 段落文本（必填）
        speaker: 归属说话人（id/key/姓名），空为未知
        time: 段落发生的绝对时间（如 '2026-08-06 14:33' 或 epoch 秒），
            空则取录制基准时间 + start_ms（无录制时为当前时间）
        recording_path: 所属录制单元路径，空为散装段落
        start_ms: 批内起始毫秒（对齐回听/切分坐标系）
        end_ms: 批内结束毫秒（>start_ms 时有效）
        part_start_ms: 所在批起点（默认 0）
    """
    if (gate := _gate()):
        return gate
    try:
        if not text.strip():
            return tool_error("text 不能为空", cause=ErrorCause.PARAM, retryable=False)
        speaker_id: Optional[int] = None
        if speaker.strip():
            target, err = await _resolve_speaker(speaker)
            if err:
                return err
            assert target is not None
            speaker_id = int(target["id"])

        store = get_audio_store()
        ts_ns = parse_time_ns(time)
        if recording_path:
            recording = await store.get_recording(recording_path)
            if not recording:
                return tool_error(f"录制单元不存在: {recording_path}",
                                  cause=ErrorCause.NOT_FOUND, retryable=False,
                                  hint="用 speaker_segments/audio_stats 先确认录制路径")
            if ts_ns is None:
                ts_ns = int(recording["started_ns"]) + start_ms * 1_000_000
        if ts_ns is None:
            if time.strip():
                return tool_error(f"无法解析 time: {time}", cause=ErrorCause.PARAM,
                                  retryable=False,
                                  hint="格式如 '2026-08-06 14:33' 或 epoch 秒")
            import time as _time
            ts_ns = _time.time_ns()

        segment_id = await store.add_segment(
            recording_path=recording_path,
            source_file="manual",
            device_source="ai",
            start_ms=start_ms,
            end_ms=max(end_ms, start_ms),
            part_start_ms=part_start_ms,
            speaker_id=speaker_id,
            transcript=text.strip(),
            ts_ns=ts_ns)
        try:
            from agent.memory.embedding import wake_embedding_worker
            wake_embedding_worker()
        except Exception:
            pass
        segment = await store.get_segment(segment_id)
        return _dump({"segment": segment})
    except Exception as e:
        return error_from_exception(e, action="新增段落")


@deferred_tool(group=_group, tags=["core", "media:voice", "media:audio"])
async def voice_listen(segment_id: int, apply: bool = False) -> str:
    """回听片段的原始音源：重新转写并比对，听不清/有疑义时订正转写与归属。

    自动定位片段对应的原始音频（沿录制合并清单切出精确切片）→ 重新转写
    → 返回 新文本 vs 旧文本 对比 + 声纹重匹配候选 + 切片文件路径
    （clip_path 在 workspace 内，可直接用其他多媒体工具继续处理）。

    Args:
        segment_id: 片段 id
        apply: true 时应用订正（文本以重转为准；声纹重匹配到不同已知人则改派归属）
    """
    if (gate := _gate()):
        return gate
    try:
        from .listen import ListenError, listen_segment
        store = get_audio_store()
        result = await listen_segment(store, segment_id, apply=apply)
        return _dump(result)
    except Exception as e:
        from .listen import ListenError
        if isinstance(e, ListenError):
            return tool_error(str(e), cause=ErrorCause.STATE, retryable=False)
        return error_from_exception(e, action=f"回听片段 [{segment_id}]")


@deferred_tool(group=_group, tags=["core"])
async def speaker_consolidate(
    threshold: float = 0.0,
    dry_run: bool = True,
    include_confirmed: bool = False,
    prune_insignificant: bool = False,
) -> str:
    """相似度合并整理 + 低价值清理：归并被分裂的临时说话人，清除环境音档案。

    解决"一场会议裂出几十个说话人"：入库单段匹配（0.75）对短段过严，
    本工具用更稳定的声纹锚 + 更宽松阈值（默认 0.70）做事后归并；
    合并后仍低价值的（命中 ≤2 且累计 ≤5s，多为环境音/背景人声）可一并剔除。
    强烈建议先 dry_run=true 预览分簇与低价值清单，确认后 dry_run=false 执行。

    Args:
        threshold: 锚相似度阈值（0 用全局配置 audio_merge_threshold，默认 0.70）
        dry_run: true 只预览不执行
        include_confirmed: 是否把已确认说话人也纳入聚类（默认只整理待确认）
        prune_insignificant: 执行时是否一并剔除低价值说话人
            （判定线可用 audio_insignificant_max_matches /
            audio_insignificant_max_audio_ms 配置调整）
    """
    if (gate := _gate()):
        return gate
    try:
        from .consolidate import consolidate
        store = get_audio_store()
        result = await consolidate(
            store,
            threshold=threshold if 0.0 < threshold < 1.0 else None,
            dry_run=dry_run,
            status="" if include_confirmed else "pending",
            prune_insignificant=prune_insignificant,
        )
        if dry_run and (result["cluster_count"] or result["insignificant"]):
            result["hint"] = ("确认无误后用 dry_run=false 执行合并"
                              "（需要清理低价值说话人时加 prune_insignificant=true）")
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="相似度合并整理")


@deferred_tool(group=_group, tags=["core"])
async def speaker_prune_pending(include_with_samples: bool = False) -> str:
    """批量剔除临时说话人（待确认归类清理）。

    Args:
        include_with_samples: false 只清理无样本且无片段的空壳档案（安全）；
            true 剔除全部待确认说话人（级联删除其样本与全部话语片段）
    """
    if (gate := _gate()):
        return gate
    try:
        store = get_audio_store()
        deleted = await store.prune_pending_speakers(
            include_with_samples=include_with_samples)
        return _dump({"pruned": len(deleted),
                      "include_with_samples": include_with_samples,
                      "speakers": deleted})
    except Exception as e:
        return error_from_exception(e, action="剔除临时说话人")


# ------------------------------------------------------------------
# 内部工具
# ------------------------------------------------------------------

_embedder: Any = None


def _get_embedder() -> Any:
    """文本域共享 Embedder（转写检索向量化用，惰性获取）。"""
    global _embedder
    if _embedder is None:
        from entities._sdk import get_embedder
        _embedder = get_embedder("text")
    return _embedder


async def _embed_query(text: str) -> Optional[List[float]]:
    """查询文本向量化；失败返回 None（检索自动降级 FTS/LIKE）。"""
    try:
        return await _get_embedder().embed_query(text)
    except Exception as exc:
        log(f"查询向量化失败（降级 FTS）: {exc}", "DEBUG", tag=_LOG_TAG)
        return None
