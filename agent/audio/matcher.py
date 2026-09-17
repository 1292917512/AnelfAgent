"""声纹匹配引擎：TopN 检索、阈值判定、多样本池累积、临时 ID 与身份合并。

阈值语义：
- 全局阈值取配置 audio_match_threshold（默认 0.75）
- 说话人档案的 threshold 字段非空时覆盖全局（单人独立阈值）
- 相似度 ≥ 阈值 → 已知人；< 阈值 → 新人（自动建临时说话人 spk_tmp_XXXX，待确认）

匹配策略：样本级 KNN（sqlite-vec / Python 余弦降级）后按说话人聚合，取该说话人
最高样本相似度作为最终得分（多样本池建模：不同场景样本提升鲁棒性）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.config import get_config_bool, get_config_float, get_config_int

from .store import AudioStore


def global_threshold() -> float:
    """全局匹配阈值（audio_match_threshold，默认 0.75）。"""
    return get_config_float("audio_match_threshold", 0.75)


def max_samples_per_speaker() -> int:
    """每说话人样本池上限（audio_max_samples_per_speaker，默认 5）。"""
    return max(1, get_config_int("audio_max_samples_per_speaker", 5))


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


async def match_vector(
    store: AudioStore,
    vector: List[float],
    *,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """声纹检索：输入 192 维向量，返回 TopK 候选（按相似度降序）。

    说话人得分 = max(最佳样本相似度, 样本池质心相似度, 质心锚相似度)：
    单样本捕捉特征峰值，池质心抑制单样本噪音，质心锚（精化固化的
    历史质心）在样本池更迭后仍保持身份连续（audio_centroid_match 可关）。
    """
    sample_hits = await store.search_sample_vectors(vector, limit=max(top_k * 5, 25))
    best_by_speaker: Dict[int, float] = {}
    for hit in sample_hits:
        speaker_id = hit["speaker_id"]
        if hit["score"] > best_by_speaker.get(speaker_id, 0.0):
            best_by_speaker[speaker_id] = hit["score"]

    # 质心匹配：均值向量与质心锚作为第二判据（锚兼独立检索来源——
    # 样本池整体更迭/淘汰后精化锚仍能把人找回来）
    if get_config_bool("audio_centroid_match", True):
        from .store import _mean_vec
        for speaker_id, anchor in await store.list_speaker_anchors():
            anchor_sim = round(_cosine_sim(vector, anchor), 4)
            if anchor_sim > best_by_speaker.get(speaker_id, 0.0):
                best_by_speaker[speaker_id] = anchor_sim
        for speaker_id in list(best_by_speaker):
            vectors = await store.get_speaker_vectors(speaker_id)
            if not vectors:
                continue
            centroid_sim = _cosine_sim(vector, _mean_vec(vectors))
            if centroid_sim > best_by_speaker[speaker_id]:
                best_by_speaker[speaker_id] = round(centroid_sim, 4)

    candidates: List[Dict[str, Any]] = []
    for speaker_id, score in best_by_speaker.items():
        speaker = await store.get_speaker(speaker_id)
        if not speaker:
            continue
        candidates.append({
            **_speaker_brief(speaker),
            "similarity": score,
            "matched": score >= effective_threshold(speaker),
        })
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates[:max(1, top_k)]


def _cosine_sim(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def identify(
    store: AudioStore,
    vector: List[float],
    *,
    audio_ms: int = 0,
    ts_ns: Optional[int] = None,
    segment_id: Optional[int] = None,
    accumulate: Optional[bool] = None,
    auto_create: Optional[bool] = None,
) -> Dict[str, Any]:
    """识别或建档：匹配已知人则命中回写并累积样本，否则创建临时说话人。

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

    candidates = await match_vector(store, vector)
    best = candidates[0] if candidates else None

    if best and best["matched"]:
        speaker_id = int(best["id"])
        await store.touch_speaker_match(speaker_id, audio_ms, ts_ns)
        sample_added = False
        if accumulate:
            new_sample_id = await store.add_sample(
                speaker_id, vector,
                segment_id=segment_id, score=float(best["similarity"]),
                source="auto", max_samples=max_samples_per_speaker())
            # -1 = 新样本被判极端拒入（outlier 策略），不触发片段挂接
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
        segment_id=segment_id, source="auto",
        max_samples=max_samples_per_speaker())
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
) -> Dict[str, Any]:
    """注册正式说话人：创建档案并将声纹样本入池。返回完整档案。"""
    speaker = await store.create_speaker(
        name=name, role=role, status="confirmed",
        notes=notes, device_source=device_source, entity_scope=entity_scope)
    await store.add_sample(
        int(speaker["id"]), vector,
        source=source, max_samples=max_samples_per_speaker())
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
    """声纹精化：样本池质心与既有质心锚融合，固化为主向量。

    采样数据越多精化越准——质心锚是超出样本池窗口（上限淘汰）的
    长期身份记忆，兼作锚检索来源。返回漂移（新旧锚余弦，首次精化
    为 None）；样本池为空时 raise ValueError。
    """
    speaker = await store.get_speaker(speaker_id)
    if not speaker:
        raise ValueError("说话人不存在")
    vectors = await store.get_speaker_vectors(speaker_id)
    if not vectors:
        raise ValueError("样本池为空，先累积语音样本再精化")
    from .store import _mean_vec
    centroid = _mean_vec(vectors)
    old = await store.get_speaker_anchor(speaker_id)
    anchor = _mean_vec([old, centroid]) if old else centroid
    drift = round(_cosine_sim(old, anchor), 4) if old else None
    await store.set_speaker_anchor(speaker_id, anchor)
    return {
        "speaker": _speaker_brief(speaker),
        "samples": len(vectors),
        "anchor_similarity": drift,
        "hint": "质心锚已更新（越接近 1 漂移越小）；匹配取 max(最佳样本, 池质心, 质心锚)",
    }


async def merge(
    store: AudioStore,
    source_id: int,
    target_id: int,
) -> Dict[str, Any]:
    """身份合并：source 并入 target。

    样本池融合（source 样本逐条入 target 池，add_sample 的淘汰策略生效）、
    片段归属重指向、统计量（时长/命中数）累加，source 档案删除。
    """
    if source_id == target_id:
        raise ValueError("合并源与目标不能是同一说话人")
    source = await store.get_speaker(source_id)
    target = await store.get_speaker(target_id)
    if not source or not target:
        raise ValueError("合并源或目标说话人不存在")

    # 样本融合：source 样本逐条入 target 池（add_sample 自带淘汰策略）
    samples = await store.list_samples(source_id)
    moved = 0
    for sample in reversed(samples):  # 按时间正序入池，保留最近样本
        vector = await store.get_sample_vector(int(sample["id"]))
        if not vector:
            continue
        new_id = await store.add_sample(
            target_id, vector,
            segment_id=sample["segment_id"], score=sample["score"],
            source="merge", max_samples=max_samples_per_speaker())
        if new_id > 0:  # -1 = outlier 策略判定为极端样本拒入
            moved += 1

    # 片段重指向 + 统计量累加 + source 删除
    await store.reassign_segments(source_id, target_id)
    await store.merge_speaker_stats(target_id, source)
    await store.delete_speaker(source_id)

    # 融合后的样本池即刻精化：合并出的身份以新质心锚为准
    refined = await refine(store, target_id)

    merged = await store.get_speaker(target_id)
    return {
        "target": merged,
        "merged_from": {"id": source["id"], "speaker_key": source["speaker_key"],
                        "name": source["name"]},
        "samples_moved": moved,
        "refined": refined,
    }
