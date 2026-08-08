"""说话人相似度合并（离线整理）：质心聚类找出同一人被分裂的临时档案。

背景：入库时的单段匹配（≥match_threshold 认亲）对短段/噪音声纹过于严格，
一场会议容易裂出大量临时说话人（一句话一个人）。本模块做事后整理：
- 每个说话人取样本池的**质心**（样本向量均值，比单段稳定）
- 质心两两余弦 ≥ merge_threshold（默认 0.70，比单段匹配宽松）的归为同簇
- 合并执行：每簇并入 累计音频时长最长 的成员（信息量最大的留下）

dry_run 模式只返回分簇预览（成员 + 簇内相似度），确认后再正式执行。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import get_config_float, get_config_int
from core.log import log

from . import matcher
from .store import VoiceprintStore


def merge_threshold() -> float:
    """质心合并阈值（voiceprint_merge_threshold，默认 0.70）。"""
    return get_config_float("voiceprint_merge_threshold", 0.70)


def insignificant_limits() -> tuple[int, int]:
    """低价值说话人判定线：(最大命中次数, 最大累计音频毫秒)。

    命中少 + 时长短的临时说话人通常是环境音/背景人声/路人，无关紧要。
    """
    return (
        get_config_int("voiceprint_insignificant_max_matches", 2),
        get_config_int("voiceprint_insignificant_max_audio_ms", 5000),
    )


def _mean_vector(vectors: List[List[float]]) -> Optional[List[float]]:
    if not vectors:
        return None
    dims = len(vectors[0])
    acc = [0.0] * dims
    count = 0
    for vec in vectors:
        if len(vec) != dims:
            continue
        for i, x in enumerate(vec):
            acc[i] += x
        count += 1
    if not count:
        return None
    return [x / count for x in acc]


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class _UnionFind:
    def __init__(self, ids: List[int]) -> None:
        self._parent = {i: i for i in ids}

    def find(self, x: int) -> int:
        parent = self._parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


async def find_merge_clusters(
    store: VoiceprintStore,
    *,
    threshold: Optional[float] = None,
    status: str = "pending",
) -> List[Dict[str, Any]]:
    """按质心相似度聚类，返回建议合并的簇（成员数 ≥2）。

    每簇：{"members": [{speaker 简报 + centroid_similarity}], "best_similarity": float}
    """
    threshold = threshold if threshold is not None else merge_threshold()
    listing = await store.list_speakers(status=status, limit=500)
    speakers = listing["items"]
    centroids: Dict[int, List[float]] = {}
    for speaker in speakers:
        vectors: List[List[float]] = []
        for sample in await store.list_samples(int(speaker["id"])):
            vec = await store.get_sample_vector(int(sample["id"]))
            if vec:
                vectors.append(vec)
        centroid = _mean_vector(vectors)
        if centroid:
            centroids[int(speaker["id"])] = centroid

    ids = list(centroids)
    uf = _UnionFind(ids)
    # 记录每个成员的最近簇内相似度（展示用）
    best_sim: Dict[int, float] = {i: 0.0 for i in ids}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            sim = _cosine(centroids[a], centroids[b])
            if sim >= threshold:
                uf.union(a, b)
                best_sim[a] = max(best_sim[a], sim)
                best_sim[b] = max(best_sim[b], sim)

    groups: Dict[int, List[int]] = {}
    for i in ids:
        groups.setdefault(uf.find(i), []).append(i)

    speaker_map = {int(s["id"]): s for s in speakers}
    clusters: List[Dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda i: speaker_map[i]["total_audio_ms"], reverse=True)
        clusters.append({
            "members": [
                {
                    "id": i,
                    "speaker_key": speaker_map[i]["speaker_key"],
                    "name": speaker_map[i]["name"],
                    "total_audio_ms": speaker_map[i]["total_audio_ms"],
                    "match_count": speaker_map[i]["match_count"],
                    "similarity": round(best_sim[i], 4),
                }
                for i in members
            ],
            "keep_id": members[0],  # 时长最长者保留
            "best_similarity": round(max(best_sim[i] for i in members), 4),
        })
    clusters.sort(key=lambda c: c["best_similarity"], reverse=True)
    return clusters


async def similarity_map(
    store: VoiceprintStore,
    *,
    status: str = "pending",
    neighbors: int = 3,
    threshold: Optional[float] = None,
    matrix_limit: int = 60,
) -> Dict[str, Any]:
    """声纹相似度分布图：按相契度聚邻排序的全景数据。

    - speakers：按所属簇分组、组内按最高相契度降序排列（相契的排在一起），
      每人附 top-N 最相似他人（AI 合并决策的直接依据）
    - clusters：merge_threshold 下的分簇（同 consolidate）
    - estimated_persons：按阈值估计的真实人数（簇总数，含单例）
    - matrix：说话人 ≤ matrix_limit 时的两两相似度矩阵（面板热力图用，
      行序与 speakers 一致，簇在视觉上自然成块）
    """
    threshold = threshold if threshold is not None else merge_threshold()
    listing = await store.list_speakers(status=status, limit=500)
    speakers = listing["items"]
    centroids: Dict[int, List[float]] = {}
    for speaker in speakers:
        vectors = await store.get_speaker_vectors(int(speaker["id"]))
        centroid = _mean_vector(vectors)
        if centroid:
            centroids[int(speaker["id"])] = centroid

    ids = list(centroids)
    # 全量两两相似度
    sims: Dict[int, Dict[int, float]] = {i: {} for i in ids}
    for x, a in enumerate(ids):
        for b in ids[x + 1:]:
            sim = round(_cosine(centroids[a], centroids[b]), 4)
            sims[a][b] = sims[b][a] = sim

    # 并查集分簇 + 估计人数
    uf = _UnionFind(ids)
    for a in ids:
        for b, sim in sims[a].items():
            if sim >= threshold:
                uf.union(a, b)
    groups: Dict[int, List[int]] = {}
    for i in ids:
        groups.setdefault(uf.find(i), []).append(i)

    speaker_map = {int(s["id"]): s for s in speakers}
    cluster_of = {i: uf.find(i) for i in ids}
    cluster_sizes = {root: len(members) for root, members in groups.items()}

    ordered = sorted(
        ids,
        key=lambda i: (
            cluster_of[i],
            -max(sims[i].values(), default=0.0),
            -speaker_map[i]["total_audio_ms"],
        ),
    )
    speaker_entries: List[Dict[str, Any]] = []
    for i in ordered:
        s = speaker_map[i]
        top = sorted(sims[i].items(), key=lambda kv: kv[1], reverse=True)[:max(1, neighbors)]
        speaker_entries.append({
            "id": i,
            "speaker_key": s["speaker_key"],
            "name": s["name"],
            "status": s["status"],
            "match_count": s["match_count"],
            "total_audio_ms": s["total_audio_ms"],
            "cluster_size": cluster_sizes[cluster_of[i]],
            "top_similar": [
                {
                    "id": j,
                    "speaker_key": speaker_map[j]["speaker_key"],
                    "name": speaker_map[j]["name"],
                    "status": speaker_map[j]["status"],
                    "similarity": sim,
                    "mergable": sim >= threshold,
                }
                for j, sim in top
            ],
        })

    matrix: Optional[Dict[str, Any]] = None
    if len(ordered) <= matrix_limit and ordered:
        matrix = {
            "order": ordered,
            "values": [[sims[a].get(b, 1.0 if a == b else 0.0) for b in ordered]
                   for a in ordered],
        }

    clusters = await find_merge_clusters(store, threshold=threshold, status=status)
    return {
        "status": status,
        "threshold": threshold,
        "speakers_total": len(ordered),
        "estimated_persons": len(groups),
        "speakers": speaker_entries,
        "clusters": clusters,
        "matrix": matrix,
    }


async def find_insignificant(
    store: VoiceprintStore,
    *,
    max_matches: Optional[int] = None,
    max_audio_ms: Optional[int] = None,
    exclude_ids: Optional[set[int]] = None,
) -> List[Dict[str, Any]]:
    """找出低价值临时说话人：命中少 + 累计时长短（环境音/背景人声/路人）。"""
    default_matches, default_audio = insignificant_limits()
    max_matches = max_matches if max_matches is not None else default_matches
    max_audio_ms = max_audio_ms if max_audio_ms is not None else default_audio
    exclude_ids = exclude_ids or set()
    listing = await store.list_speakers(status="pending", limit=500)
    result: List[Dict[str, Any]] = []
    for speaker in listing["items"]:
        if int(speaker["id"]) in exclude_ids:
            continue
        if speaker["match_count"] <= max_matches \
                and speaker["total_audio_ms"] <= max_audio_ms:
            result.append({
                "id": speaker["id"],
                "speaker_key": speaker["speaker_key"],
                "name": speaker["name"],
                "match_count": speaker["match_count"],
                "total_audio_ms": speaker["total_audio_ms"],
            })
    return result


async def consolidate(
    store: VoiceprintStore,
    *,
    threshold: Optional[float] = None,
    dry_run: bool = True,
    status: str = "pending",
    prune_insignificant: bool = False,
) -> Dict[str, Any]:
    """相似度合并整理 + 低价值清理。

    合并：每簇并入累计音频时长最长的成员（dry_run=True 只预览）。
    清理：合并后仍低价值的临时说话人（命中少+时长短，多为环境音），
    prune_insignificant=True 且 dry_run=False 时一并剔除。
    """
    clusters = await find_merge_clusters(store, threshold=threshold, status=status)
    merges: List[Dict[str, Any]] = []
    if not dry_run:
        for cluster in clusters:
            keep_id = int(cluster["keep_id"])
            for member in cluster["members"]:
                if int(member["id"]) == keep_id:
                    continue
                try:
                    result = await matcher.merge(store, int(member["id"]), keep_id)
                    merges.append({
                        "from": member["speaker_key"],
                        "into": result["target"]["speaker_key"] if result["target"] else "",
                        "samples_moved": result["samples_moved"],
                    })
                except ValueError as exc:
                    log(f"合并跳过 [{member['speaker_key']}]: {exc}", "WARNING", tag="音源库")

    # 低价值候选（执行合并后重新评估）。合并涉及的所有人（被并掉的 + 保留者）
    # 都必须排除：保留者若被误清，刚并入的片段会随删除级联变成未知说话人
    if not dry_run:
        cluster_ids = {int(m["id"]) for c in clusters for m in c["members"]}
    else:
        cluster_ids = set()
    insignificant = await find_insignificant(store, exclude_ids=cluster_ids)
    pruned: List[Dict[str, Any]] = []
    if prune_insignificant and not dry_run and insignificant:
        for speaker in insignificant:
            deleted = await store.delete_speaker(int(speaker["id"]))
            if deleted:
                pruned.append(speaker)

    return {
        "dry_run": dry_run,
        "threshold": threshold if threshold is not None else merge_threshold(),
        "clusters": clusters,
        "cluster_count": len(clusters),
        "speakers_affected": sum(len(c["members"]) for c in clusters),
        "merges": merges,
        "insignificant": insignificant,
        "insignificant_limits": {
            "max_matches": insignificant_limits()[0],
            "max_audio_ms": insignificant_limits()[1],
        },
        "pruned": pruned,
    }
