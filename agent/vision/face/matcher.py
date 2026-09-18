"""人脸匹配引擎：锚扫描候选、双判据评分、分离度门与识别建档。

人脸模型（一人一档案，多样本聚合，与声纹库同构）：
- 每个人物维护一个人脸锚——全部历史合格样本的质量加权质心，随每次
  合格采样增量折叠（学习率随累积量自然衰减，即"采样越多越准"），
  是身份的长期记忆与匹配主判据；
- 样本池是带来源标注的近期多样本窗口（聊天图/截图/注册照的姿态与
  光照各异），支撑最佳样本判据；
- 人物得分 = max(锚相似度, 最佳样本相似度)：锚抑制单样本噪音，
  最佳样本保留特征峰值；
- 分离度门（AS-Norm，与声纹同一打分后端）：候选得分还需高出本次查询
  的冒充分布（对其余人物锚得分做 z 归一）——"很多人都像"的模糊查询
  降级为临时人物待确认，而不是冒险认亲（防投毒优先于防分裂）。

阈值语义（ArcFace 余弦，与声纹阈值体系不可互换）：
- 全局阈值 face_match_threshold（默认 0.40，buffalo_l/ArcFace-512 的典型
  判定区间为 0.3~0.5，部署后应以实际库标定）；人物 threshold 非空时覆盖
- 相似度 ≥ 阈值，且分离度门启用时 z 分值 ≥ face_separation
  → 已知人；否则新人（自动建临时人物 fc_tmp_XXXX，待确认）
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.config import get_config_bool, get_config_float, get_config_int

from .store import FaceStore
from .vectors import blend, cosine, cosine_many, unit_rows, weighted_centroid

# 锚扫描入围：距全局阈值的边距（覆盖更宽松的独立阈值）+ TopN 进精评
_SHORTLIST_MARGIN = 0.15
_SHORTLIST_TOP = 8
# AS-Norm 冒充 cohort 的 top-K 上限（人物量大于此值时取最高分的前 K 个）
_COHORT_TOP_K = 100


def global_threshold() -> float:
    """全局匹配阈值（face_match_threshold，默认 0.40）。"""
    return get_config_float("face_match_threshold", 0.40)


def separation_floor() -> float:
    """判识分离度门槛（face_separation，默认 2.0，0=关闭）。"""
    return get_config_float("face_separation", 2.0)


def merge_threshold() -> float:
    """锚合并判读线（face_merge_threshold，默认 0.55，比匹配阈值宽松）。"""
    return get_config_float("face_merge_threshold", 0.55)


def max_samples_per_person() -> int:
    """每人物样本池上限（face_max_samples_per_person，默认 10）。"""
    return max(1, get_config_int("face_max_samples_per_person", 10))


def effective_threshold(person: Dict[str, Any]) -> float:
    """人物有效阈值：独立阈值优先，否则全局阈值。"""
    threshold = person.get("threshold")
    if threshold is not None:
        return float(threshold)
    return global_threshold()


def _person_brief(person: Dict[str, Any]) -> Dict[str, Any]:
    """匹配结果中的人物简报。"""
    return {
        "id": person["id"],
        "person_key": person["person_key"],
        "name": person["name"],
        "role": person["role"],
        "status": person["status"],
        "entity_scope": person.get("entity_scope", ""),
        "threshold": effective_threshold(person),
    }


def _cohort_separation(person_pos: int, anchor_sims: np.ndarray) -> Optional[float]:
    """AS-Norm z 分值：候选得分相对本次查询冒充分布的偏离。

    cohort = 其余人物的锚得分（量大时取 top-K），z = (s−μ)/σ。
    cohort 不足 3 人时返回 None（分布无统计意义，门自动不启用）。
    """
    cohort = np.delete(anchor_sims, person_pos)
    if len(cohort) < 3:
        return None
    cohort = np.sort(cohort)[::-1][:_COHORT_TOP_K]
    std = float(cohort.std())
    return float((anchor_sims[person_pos] - cohort.mean()) / max(std, 1e-6))


async def match_vector(
    store: FaceStore,
    vector: List[float],
    *,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """人脸检索：锚全量矩阵扫描入围，候选按双判据 + 分离度门精评。

    入围 = 锚相似度 ≥ 全局阈值 - 边距 或 锚相似度 TopN；精评才加载
    样本池（锚是主判据，池只在入围者上展开）。
    """
    ids, matrix = await store.person_anchor_matrix()
    if not ids:
        return []
    query = np.asarray(vector, dtype=np.float64)
    if not np.any(query):
        return []
    sims = cosine_many(matrix, query)

    floor = global_threshold() - _SHORTLIST_MARGIN
    ranked = sorted(zip(ids, sims.tolist(), strict=True),
                    key=lambda kv: kv[1], reverse=True)
    shortlist = [pid for pid, sim in ranked if sim >= floor][:max(_SHORTLIST_TOP, top_k)]
    if not shortlist:
        shortlist = [pid for pid, _ in ranked[:top_k]]
    pos_of = {pid: i for i, pid in enumerate(ids)}
    margin = separation_floor()

    candidates: List[Dict[str, Any]] = []
    for person_id in shortlist:
        person = await store.get_person(person_id)
        if not person:
            continue
        anchor_sim = float(sims[pos_of[person_id]])
        sample_sim = 0.0
        samples = await store.get_person_samples(person_id)
        if samples:
            pool_matrix = np.asarray([vec for vec, _, _ in samples], dtype=np.float64)
            sample_sim = float(cosine_many(pool_matrix, vector).max())

        score = max(anchor_sim, sample_sim)
        separation = _cohort_separation(pos_of[person_id], sims) if margin > 0 else None
        threshold = effective_threshold(person)
        matched = bool(score >= threshold)
        if matched and separation is not None:
            matched = separation >= margin
        candidates.append({
            **_person_brief(person),
            "similarity": round(score, 4),
            "matched": matched,
            "anchor_similarity": round(anchor_sim, 4),
            "sample_similarity": round(sample_sim, 4),
            "separation": round(separation, 3) if separation is not None else None,
        })
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates[:max(1, top_k)]


async def identify(
    store: FaceStore,
    vector: List[float],
    *,
    det_score: float = 0.0,
    bbox: Optional[List[float]] = None,
    pose: Optional[Dict[str, Any]] = None,
    image_path: str = "",
    source: str = "",
    event_id: Optional[int] = None,
    ts_ns: Optional[int] = None,
    accumulate: Optional[bool] = None,
    auto_create: Optional[bool] = None,
) -> Dict[str, Any]:
    """识别或建档：匹配已知人则命中回写并累积样本，否则创建临时人物。

    Returns:
        {
            "person": {...},         # 归属人物简报
            "similarity": float,     # 与归属人物的相似度（新人为 0）
            "is_new": bool,          # 是否新人
            "sample_added": bool,    # 本次是否新增了人脸样本
            "candidates": [...],     # TopK 候选（含未达标者，供人工裁决）
        }
    """
    if ts_ns is None:
        ts_ns = time.time_ns()
    if accumulate is None:
        accumulate = get_config_bool("face_auto_accumulate", True)
    if auto_create is None:
        auto_create = get_config_bool("face_auto_create_unknown", True)

    candidates = await match_vector(store, vector)
    best = candidates[0] if candidates else None

    if best and best["matched"]:
        person_id = int(best["id"])
        await store.touch_person_match(person_id, ts_ns)
        sample_id = -1
        if accumulate:
            sample_id = await store.add_sample(
                person_id, vector, image_path=image_path, bbox=bbox, pose=pose,
                det_score=det_score, source=source, event_id=event_id)
        return {
            "person": best,
            "similarity": float(best["similarity"]),
            "is_new": False,
            # -1 = 相干门拒入（错认人/劣质脸防投毒）
            "sample_added": sample_id > 0,
            "sample_id": sample_id,
            "candidates": candidates,
        }

    # 未匹配到已知人：创建临时人物（待确认）
    if not auto_create:
        return {"person": None, "similarity": 0.0, "is_new": True,
                "sample_added": False, "sample_id": -1, "candidates": candidates}
    person = await store.create_person(status="pending")
    sample_id = await store.add_sample(
        int(person["id"]), vector, image_path=image_path, bbox=bbox, pose=pose,
        det_score=det_score, source=source, event_id=event_id)
    await store.touch_person_match(int(person["id"]), ts_ns)
    return {
        "person": _person_brief(person),
        "similarity": 0.0,
        "is_new": True,
        "sample_added": sample_id > 0,
        "sample_id": sample_id,
        "candidates": candidates,
    }


async def enroll(
    store: FaceStore,
    name: str,
    vector: List[float],
    *,
    role: str = "",
    notes: str = "",
    entity_scope: str = "",
    det_score: float = 0.0,
    bbox: Optional[List[float]] = None,
    pose: Optional[Dict[str, Any]] = None,
    image_path: str = "",
    source: str = "enroll",
) -> Dict[str, Any]:
    """注册人物：同名已确认档案直接累积样本（一人一档案），否则建档。

    向既有档案累积时样本仍过相干门——脸对不上的注册会被拒入
    （返回的 sample_rejected 标记），防止张冠李戴。
    """
    existing = None
    for match in await store.find_persons(name):
        if match["name"] == name and match["status"] == "confirmed":
            existing = match
            break
    if existing is not None:
        sample_id = await store.add_sample(
            int(existing["id"]), vector, image_path=image_path, bbox=bbox,
            pose=pose, det_score=det_score, source=source)
        result = await store.get_person(int(existing["id"]))
        assert result is not None
        return {**result, "sample_rejected": sample_id < 0}

    person = await store.create_person(
        name=name, role=role, status="confirmed",
        notes=notes, entity_scope=entity_scope)
    await store.add_sample(
        int(person["id"]), vector, image_path=image_path, bbox=bbox,
        pose=pose, det_score=det_score, source=source)
    result = await store.get_person(int(person["id"]))
    assert result is not None
    return result


async def enroll_samples(
    store: FaceStore,
    name: str,
    detections: List[Tuple[List[float], float, Optional[List[float]],
                           Optional[Dict[str, Any]]]],
    *,
    role: str = "",
    notes: str = "",
    entity_scope: str = "",
    image_path: str = "",
    source: str = "enroll",
) -> Dict[str, Any]:
    """注册一张图/一批向量的多条人脸样本：首条建档/累积，其余入池。

    detections 为 [(向量, det_score, bbox, pose)]，超出样本池上限的部分舍弃。
    返回 {"person", "samples_enrolled", "sample_rejected"}——
    sample_rejected 为 True 表示有样本被相干门拒入（脸对不上）。
    """
    first = detections[0]
    person = await enroll(
        store, name, first[0], role=role, notes=notes, entity_scope=entity_scope,
        det_score=first[1], bbox=first[2], pose=first[3],
        image_path=image_path, source=source)
    rejected = bool(person.pop("sample_rejected", False))
    enrolled = 1
    for vec, det_score, bbox, pose in detections[1:max_samples_per_person()]:
        added = await store.add_sample(
            int(person["id"]), vec, image_path=image_path, bbox=bbox,
            pose=pose, det_score=det_score, source=source)
        rejected = rejected or added < 0
        enrolled += 1
    return {"person": person, "samples_enrolled": enrolled,
            "sample_rejected": rejected}


async def confirm(
    store: FaceStore,
    person_id: int,
    name: str,
    *,
    role: str = "",
) -> Optional[Dict[str, Any]]:
    """确认临时人物：赋予正式姓名并转为 confirmed 状态。"""
    return await store.update_person(
        person_id, name=name, status="confirmed", role=role or None)


async def refine(store: FaceStore, person_id: int) -> Dict[str, Any]:
    """人脸锚重建：以当前样本池重立锚（丢弃被污染的历史累积）。

    锚随采样自动进化，常规情况无需重建；适用场景是手动剔除坏样本后
    复位、或怀疑锚被长期误匹配带偏。返回漂移（新旧锚余弦）；
    样本池为空时 raise ValueError。
    """
    person = await store.get_person(person_id)
    if not person:
        raise ValueError("人物不存在")
    samples = await store.get_person_samples(person_id)
    if not samples:
        raise ValueError("样本池为空，先累积人脸样本再重建")
    centroid = weighted_centroid([(vec, weight) for vec, weight, _ in samples])
    assert centroid is not None
    anchor, anchor_weight = centroid
    old, _ = await store.get_person_anchor(person_id)
    drift = round(cosine(old, anchor), 4) if old else None
    await store.set_person_anchor(person_id, anchor, anchor_weight)
    return {
        "person": _person_brief(person),
        "samples": len(samples),
        "anchor_similarity": drift,
        "hint": "人脸锚已按当前样本池重建（漂移越接近 1 变化越小）；"
                "匹配取 max(锚, 最佳样本)",
    }


async def compare(store: FaceStore, person_id_a: int, person_id_b: int) -> Dict[str, Any]:
    """精确对比两个人物的人脸：锚余弦 + 样本最佳配对。

    Returns:
        {
            "persons": {...},                    # 双方简报
            "anchor_similarity": float,          # 锚对锚（长期身份的相契度）
            "best_sample_similarity": float|None,  # 样本池最佳配对（峰值证据）
            "merge_hint": str,                   # 相对合并阈值的判读
        }
    """
    person_a = await store.get_person(person_id_a)
    person_b = await store.get_person(person_id_b)
    if not person_a or not person_b:
        raise ValueError("对比的人物不存在")
    samples_a = await store.get_person_samples(person_id_a)
    samples_b = await store.get_person_samples(person_id_b)
    anchor_a, _ = await store.get_person_anchor(person_id_a)
    anchor_b, _ = await store.get_person_anchor(person_id_b)
    anchor_sim = cosine(anchor_a, anchor_b) if anchor_a and anchor_b else None

    best_sample: Optional[float] = None
    if samples_a and samples_b:
        matrix_a = np.asarray([v for v, _, _ in samples_a], dtype=np.float64)
        matrix_b = np.asarray([v for v, _, _ in samples_b], dtype=np.float64)
        best_sample = float((unit_rows(matrix_a) @ unit_rows(matrix_b).T).max())

    merge_floor = merge_threshold()
    scores = [s for s in (anchor_sim, best_sample) if s is not None]
    best = max(scores) if scores else 0.0
    verdict = "建议合并（达到合并阈值）" if best >= merge_floor else "相契度不足，谨慎合并"
    return {
        "persons": {"a": _person_brief(person_a), "b": _person_brief(person_b)},
        "anchor_similarity": round(anchor_sim, 4) if anchor_sim is not None else None,
        "best_sample_similarity": round(best_sample, 4) if best_sample is not None else None,
        "merge_threshold": merge_floor,
        "merge_hint": f"最高相契度 {round(best, 4)}（判读线 {merge_floor}）：{verdict}",
    }


async def merge(store: FaceStore, source_id: int, target_id: int) -> Dict[str, Any]:
    """身份合并：source 并入 target（一人一档案的归一路径）。

    样本池整体迁移（池满按最近保留）、锚按累计权重精确合成（加权质心
    的结合律：与重放两档案全部历史样本等价）、统计量累加，source 档案
    删除（出现事件保留，历史归属信息在 faces_json 中变为陈旧引用）。
    """
    if source_id == target_id:
        raise ValueError("合并源与目标不能是同一人物")
    source = await store.get_person(source_id)
    target = await store.get_person(target_id)
    if not source or not target:
        raise ValueError("合并源或目标人物不存在")

    moved = await store.move_samples(source_id, target_id)

    target_anchor, target_weight = await store.get_person_anchor(target_id)
    source_anchor, source_weight = await store.get_person_anchor(source_id)
    drift: Optional[float] = None
    if source_anchor:
        if target_anchor:
            merged, weight = blend(target_anchor, target_weight,
                                   source_anchor, source_weight)
            drift = round(cosine(target_anchor, merged), 4)
        else:
            merged, weight = list(source_anchor), source_weight
        await store.set_person_anchor(target_id, merged, weight)

    await store.merge_person_stats(target_id, source)
    await store.delete_person(source_id)

    merged_person = await store.get_person(target_id)
    return {
        "target": merged_person,
        "merged_from": {"id": source["id"], "person_key": source["person_key"],
                        "name": source["name"]},
        "samples_moved": moved,
        "anchor_similarity": drift,
    }
