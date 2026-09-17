"""声纹匹配引擎：锚扫描候选、信道感知评分、分离度门与识别建档。

声纹模型（一人一档案，多样本聚合）：
- 每个说话人维护一个声纹锚——全部历史合格样本的时长加权质心，随每次
  合格采样增量折叠（学习率随累积量自然衰减，即"采样越多越准"的低学习率
  动态更新），是身份的长期记忆与匹配主判据；
- 样本池是带信道标注的近期多样本窗口，支撑信道模板（同一人经微信/电话/
  麦克风提取的嵌入存在信道漂移，同信道质心可补偿）与最佳样本判据；
- 说话人得分 = max(锚相似度, 信道模板相似度, 最佳样本相似度)：锚抑制
  单样本噪音，信道模板补偿跨设备漂移，最佳样本保留特征峰值；
- 分离度门（AS-Norm，业界标准打分后端）：候选得分还需高出本次查询的
  冒充者分布（对其余说话人锚得分做 z 归一）——"很多人 都像"的模糊查询
  降级为临时说话人待确认，而不是冒险认亲（防投毒优先于防分裂）。

阈值语义：
- 全局阈值 audio_match_threshold（默认 0.75）；说话人 threshold 非空时覆盖
- 相似度 ≥ 阈值 且（分离度不足门槛或冒充 cohort <3 时仅阈值）→ 已知人；
  否则新人（自动建临时说话人 spk_tmp_XXXX，待确认）
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.config import get_config_bool, get_config_float, get_config_int

from .store import AudioStore
from .vectors import blend, cosine, cosine_many, sample_weight, weighted_centroid

# 锚扫描入围：距全局阈值的边距（覆盖更宽松的独立阈值）+ TopN 进精评
_SHORTLIST_MARGIN = 0.25
_SHORTLIST_TOP = 8
# 信道模板生效所需的最少同信道样本数
_MIN_CHANNEL_SAMPLES = 2
# AS-Norm 冒充 cohort 的 top-K 上限（说话人量大于此值时取最高分的前 K 个）
_COHORT_TOP_K = 100


def global_threshold() -> float:
    """全局匹配阈值（audio_match_threshold，默认 0.75）。"""
    return get_config_float("audio_match_threshold", 0.75)


def separation_floor() -> float:
    """判识分离度门槛（audio_match_separation，默认 2.0，0=关闭）。

    AS-Norm z 分值：候选锚得分相对本次查询冒充分布（其余说话人
    top-K 得分的均值/标准差）的偏离量。"""
    return get_config_float("audio_match_separation", 2.0)


def max_samples_per_speaker() -> int:
    """每说话人样本池上限（audio_max_samples_per_speaker，默认 10）。"""
    return max(1, get_config_int("audio_max_samples_per_speaker", 10))


def effective_threshold(speaker: Dict[str, Any]) -> float:
    """说话人有效阈值：独立阈值优先，否则全局阈值。"""
    threshold = speaker.get("threshold")
    if threshold is not None:
        return float(threshold)
    return global_threshold()


def _speaker_brief(speaker: Dict[str, Any]) -> Dict[str, Any]:
    """匹配结果中的说话人简报。"""
    return {
        "id": speaker["id"],
        "speaker_key": speaker["speaker_key"],
        "name": speaker["name"],
        "role": speaker["role"],
        "status": speaker["status"],
        "entity_scope": speaker.get("entity_scope", ""),
        "threshold": effective_threshold(speaker),
    }


def _cohort_separation(
    speaker_pos: int, anchor_sims: np.ndarray,
) -> Optional[float]:
    """AS-Norm z 分值：候选得分相对本次查询冒充分布的偏离。

    cohort = 其余说话人的锚得分（量大时取 top-K），z = (s−μ)/σ。
    cohort 不足 3 人时返回 None（分布无统计意义，门自动不启用）。
    """
    cohort = np.delete(anchor_sims, speaker_pos)
    if len(cohort) < 3:
        return None
    cohort = np.sort(cohort)[::-1][:_COHORT_TOP_K]
    std = float(cohort.std())
    return float((anchor_sims[speaker_pos] - cohort.mean()) / max(std, 1e-6))


async def match_vector(
    store: AudioStore,
    vector: List[float],
    *,
    channel: str = "",
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """声纹检索：锚全量矩阵扫描入围，候选按三判据 + 分离度门精评。

    入围 = 锚相似度 ≥ 全局阈值 - 边距 或 锚相似度 TopN；精评才加载
    样本池（锚是主判据，池只在入围者上展开）。
    """
    ids, matrix = await store.speaker_anchor_matrix()
    if not ids:
        return []
    query = np.asarray(vector, dtype=np.float64)
    query_norm = float(np.linalg.norm(query))
    if query_norm == 0.0:
        return []
    sims = cosine_many(matrix, vector)

    floor = global_threshold() - _SHORTLIST_MARGIN
    ranked = sorted(zip(ids, sims.tolist(), strict=True),
                    key=lambda kv: kv[1], reverse=True)
    shortlist = [sid for sid, sim in ranked if sim >= floor][:max(_SHORTLIST_TOP, top_k)]
    if not shortlist:
        shortlist = [sid for sid, _ in ranked[:top_k]]
    pos_of = {sid: i for i, sid in enumerate(ids)}
    margin = separation_floor()

    candidates: List[Dict[str, Any]] = []
    for speaker_id in shortlist:
        speaker = await store.get_speaker(speaker_id)
        if not speaker:
            continue
        anchor_sim = float(sims[pos_of[speaker_id]])
        sample_sim = 0.0
        channel_sim: Optional[float] = None
        samples = await store.get_speaker_samples(speaker_id)
        if samples:
            pool_matrix = np.asarray([vec for vec, _, _ in samples], dtype=np.float64)
            sample_sims = cosine_many(pool_matrix, vector)
            sample_sim = float(sample_sims.max())
            if channel:
                channel_idx = [
                    i for i, (_, _, sample_channel) in enumerate(samples)
                    if sample_channel == channel]
                if len(channel_idx) >= _MIN_CHANNEL_SAMPLES:
                    centroid = weighted_centroid(
                        [(samples[i][0], sample_weight(samples[i][1])) for i in channel_idx])
                    if centroid is not None:
                        channel_sim = cosine(vector, centroid[0])

        score = max(anchor_sim, sample_sim, channel_sim or 0.0)
        separation = _cohort_separation(pos_of[speaker_id], sims) if margin > 0 else None
        threshold = effective_threshold(speaker)
        matched = bool(score >= threshold)
        if matched and separation is not None:
            matched = separation >= margin
        candidates.append({
            **_speaker_brief(speaker),
            "similarity": round(score, 4),
            "matched": matched,
            "channel": channel,
            "anchor_similarity": round(anchor_sim, 4),
            "sample_similarity": round(sample_sim, 4),
            "channel_similarity": round(channel_sim, 4) if channel_sim is not None else None,
            "separation": round(separation, 3) if separation is not None else None,
        })
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates[:max(1, top_k)]


async def identify(
    store: AudioStore,
    vector: List[float],
    *,
    audio_ms: int = 0,
    ts_ns: Optional[int] = None,
    segment_id: Optional[int] = None,
    channel: str = "",
    accumulate: Optional[bool] = None,
    auto_create: Optional[bool] = None,
) -> Dict[str, Any]:
    """识别或建档：匹配已知人则命中回写并累积样本，否则创建临时说话人。

    channel 为本次语音的信道（voip/mic/web/enroll...），参与信道模板
    评分并随样本入池。

    Returns:
        {
            "speaker": {...},        # 归属说话人简报
            "similarity": float,     # 与归属说话人的相似度（新人为 0）
            "is_new": bool,          # 是否新人
            "sample_added": bool,    # 本次是否新增了声纹样本（ingest 挂接片段的依据）
            "candidates": [...],     # TopK 候选（含未达标者，供人工裁决）
        }
    """
    if ts_ns is None:
        ts_ns = time.time_ns()
    if accumulate is None:
        accumulate = get_config_bool("audio_auto_accumulate", True)
    if auto_create is None:
        auto_create = get_config_bool("audio_auto_create_unknown", True)

    candidates = await match_vector(store, vector, channel=channel)
    best = candidates[0] if candidates else None

    if best and best["matched"]:
        speaker_id = int(best["id"])
        await store.touch_speaker_match(speaker_id, audio_ms, ts_ns)
        sample_added = False
        if accumulate:
            new_sample_id = await store.add_sample(
                speaker_id, vector,
                segment_id=segment_id, channel=channel, duration_ms=audio_ms,
                score=float(best["similarity"]), source="auto")
            # -1 = 相干门拒入（错认人/噪音防投毒），不触发片段挂接
            sample_added = new_sample_id > 0
        return {
            "speaker": best,
            "similarity": float(best["similarity"]),
            "is_new": False,
            "sample_added": sample_added,
            "candidates": candidates,
        }

    # 未匹配到已知人：创建临时说话人（待确认）
    if not auto_create:
        return {"speaker": None, "similarity": 0.0, "is_new": True,
                "sample_added": False, "candidates": candidates}
    speaker = await store.create_speaker(status="pending")
    await store.add_sample(
        int(speaker["id"]), vector,
        segment_id=segment_id, channel=channel, duration_ms=audio_ms, source="auto")
    await store.touch_speaker_match(int(speaker["id"]), audio_ms, ts_ns)
    return {
        "speaker": _speaker_brief(speaker),
        "similarity": 0.0,
        "is_new": True,
        "sample_added": True,
        "candidates": candidates,
    }


async def enroll(
    store: AudioStore,
    name: str,
    vector: List[float],
    *,
    role: str = "",
    notes: str = "",
    device_source: str = "",
    entity_scope: str = "",
    source: str = "enroll",
    channel: str = "enroll",
    duration_ms: int = 0,
) -> Dict[str, Any]:
    """注册说话人：同名已确认档案直接累积样本（一人一档案），否则建档。

    向既有档案累积时样本仍过相干门——声音对不上的注册会被拒入
    （返回的 sample_rejected 标记），防止张冠李戴。
    """
    existing = None
    for match in await store.find_speakers(name):
        if match["name"] == name and match["status"] == "confirmed":
            existing = match
            break
    if existing is not None:
        sample_id = await store.add_sample(
            int(existing["id"]), vector, source=source,
            channel=channel, duration_ms=duration_ms)
        result = await store.get_speaker(int(existing["id"]))
        assert result is not None
        return {**result, "sample_rejected": sample_id < 0}

    speaker = await store.create_speaker(
        name=name, role=role, status="confirmed",
        notes=notes, device_source=device_source, entity_scope=entity_scope)
    await store.add_sample(
        int(speaker["id"]), vector,
        source=source, channel=channel, duration_ms=duration_ms)
    result = await store.get_speaker(int(speaker["id"]))
    assert result is not None
    return result


async def confirm(
    store: AudioStore,
    speaker_id: int,
    name: str,
    *,
    role: str = "",
) -> Optional[Dict[str, Any]]:
    """确认临时说话人：赋予正式姓名并转为 confirmed 状态。"""
    return await store.update_speaker(
        speaker_id, name=name, status="confirmed", role=role or None)


async def refine(
    store: AudioStore,
    speaker_id: int,
) -> Dict[str, Any]:
    """声纹重建：以当前样本池重立声纹锚（丢弃被污染的历史累积）。

    锚随采样自动进化，常规情况无需重建；适用场景是手动剔除坏样本后
    复位、或怀疑锚被长期误匹配带偏。返回漂移（新旧锚余弦）；
    样本池为空时 raise ValueError。
    """
    speaker = await store.get_speaker(speaker_id)
    if not speaker:
        raise ValueError("说话人不存在")
    samples = await store.get_speaker_samples(speaker_id)
    if not samples:
        raise ValueError("样本池为空，先累积语音样本再重建")
    centroid = weighted_centroid(
        [(vec, sample_weight(duration_ms)) for vec, duration_ms, _ in samples])
    assert centroid is not None
    anchor, anchor_weight = centroid
    old, _ = await store.get_speaker_anchor(speaker_id)
    drift = round(cosine(old, anchor), 4) if old else None
    await store.set_speaker_anchor(speaker_id, anchor, anchor_weight)
    return {
        "speaker": _speaker_brief(speaker),
        "samples": len(samples),
        "anchor_similarity": drift,
        "hint": "声纹锚已按当前样本池重建（漂移越接近 1 变化越小）；"
                "匹配取 max(锚, 信道模板, 最佳样本)",
    }


async def compare(
    store: AudioStore,
    speaker_id_a: int,
    speaker_id_b: int,
) -> Dict[str, Any]:
    """精确对比两个说话人的声纹：锚余弦 + 样本最佳配对 + 同信道模板交叉。

    Returns:
        {
            "speakers": {...},           # 双方简报（含样本数/信道分布/实体绑定）
            "anchor_similarity": float,  # 锚对锚（长期身份的相契度）
            "best_sample_similarity": float | None,  # 样本池最佳配对（峰值证据）
            "channels": {channel: {"similarity", "samples"}},  # 同信道模板交叉
            "merge_hint": str,           # 相对合并阈值的判读
        }
    """
    speaker_a = await store.get_speaker(speaker_id_a)
    speaker_b = await store.get_speaker(speaker_id_b)
    if not speaker_a or not speaker_b:
        raise ValueError("对比的说话人不存在")
    samples_a = await store.get_speaker_samples(speaker_id_a)
    samples_b = await store.get_speaker_samples(speaker_id_b)
    anchor_a, _ = await store.get_speaker_anchor(speaker_id_a)
    anchor_b, _ = await store.get_speaker_anchor(speaker_id_b)
    anchor_sim = cosine(anchor_a, anchor_b) if anchor_a and anchor_b else None

    best_sample: Optional[float] = None
    if samples_a and samples_b:
        from .vectors import unit_rows
        matrix_a = np.asarray([v for v, _, _ in samples_a], dtype=np.float64)
        matrix_b = np.asarray([v for v, _, _ in samples_b], dtype=np.float64)
        best_sample = float((unit_rows(matrix_a) @ unit_rows(matrix_b).T).max())

    def _centroids(samples: List[Tuple[List[float], int, str]]) -> Dict[str, List[float]]:
        grouped: Dict[str, List[Tuple[List[float], float]]] = {}
        for vec, duration_ms, channel in samples:
            grouped.setdefault(channel or "mic", []).append(
                (vec, sample_weight(duration_ms)))
        result: Dict[str, List[float]] = {}
        for channel, pairs in grouped.items():
            centroid = weighted_centroid(pairs)
            if centroid is not None:
                result[channel] = centroid[0]
        return result

    channels: Dict[str, Any] = {}
    cent_a, cent_b = _centroids(samples_a), _centroids(samples_b)
    counts_a: Dict[str, int] = {}
    counts_b: Dict[str, int] = {}
    for _, _, channel in samples_a:
        counts_a[channel or "mic"] = counts_a.get(channel or "mic", 0) + 1
    for _, _, channel in samples_b:
        counts_b[channel or "mic"] = counts_b.get(channel or "mic", 0) + 1
    for channel in sorted(set(cent_a) & set(cent_b)):
        channels[channel] = {
            "similarity": round(cosine(cent_a[channel], cent_b[channel]), 4),
            "samples": [counts_a.get(channel, 0), counts_b.get(channel, 0)],
        }

    merge_floor = get_config_float("audio_merge_threshold", 0.70)
    best = max(x for x in (anchor_sim, best_sample,
                           *(c["similarity"] for c in channels.values()))
               if x is not None) if (anchor_sim or best_sample or channels) else 0.0
    verdict = "建议合并（达到合并阈值）" if best >= merge_floor else "相契度不足，谨慎合并"
    return {
        "speakers": {
            "a": _speaker_brief(speaker_a),
            "b": _speaker_brief(speaker_b),
        },
        "anchor_similarity": round(anchor_sim, 4) if anchor_sim is not None else None,
        "best_sample_similarity": round(best_sample, 4) if best_sample is not None else None,
        "channels": channels,
        "merge_threshold": merge_floor,
        "merge_hint": f"最高相契度 {round(best, 4)}（判读线 {merge_floor}）：{verdict}",
    }


async def merge(
    store: AudioStore,
    source_id: int,
    target_id: int,
) -> Dict[str, Any]:
    """身份合并：source 并入 target（一人一档案的归一路径）。

    样本池整体迁移（保留信道/时长，池满按最近保留）、锚按累计权重精确
    合成（加权质心的结合律：与重放两档案全部历史样本等价）、片段归属
    重指向、统计量累加，source 档案删除。
    """
    if source_id == target_id:
        raise ValueError("合并源与目标不能是同一说话人")
    source = await store.get_speaker(source_id)
    target = await store.get_speaker(target_id)
    if not source or not target:
        raise ValueError("合并源或目标说话人不存在")

    moved = await store.move_samples(source_id, target_id)

    target_anchor, target_weight = await store.get_speaker_anchor(target_id)
    source_anchor, source_weight = await store.get_speaker_anchor(source_id)
    drift: Optional[float] = None
    if source_anchor:
        if target_anchor:
            merged, weight = blend(
                target_anchor, target_weight, source_anchor, source_weight)
            drift = round(cosine(target_anchor, merged), 4)
        else:
            merged, weight = list(source_anchor), source_weight
        await store.set_speaker_anchor(target_id, merged, weight)

    await store.reassign_segments(source_id, target_id)
    await store.merge_speaker_stats(target_id, source)
    await store.delete_speaker(source_id)

    merged_speaker = await store.get_speaker(target_id)
    return {
        "target": merged_speaker,
        "merged_from": {"id": source["id"], "speaker_key": source["speaker_key"],
                        "name": source["name"]},
        "samples_moved": moved,
        "anchor_similarity": drift,
    }
