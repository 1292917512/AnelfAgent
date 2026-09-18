"""人物相似度合并（离线整理）：锚聚类找出同一人被分裂的临时档案。

背景：入库时的单图匹配（≥match_threshold 认亲）对小脸/侧脸/糊脸过于
严格，群聊图容易裂出大量临时人物（一张路人脸一个档案）。本模块做事后
整理（与声纹库 consolidate 同一范式）：
- 每个人物取人脸锚（历史合格样本的质量加权质心，比单样本稳定得多）
- 锚两两余弦 ≥ merge_threshold（默认 0.55，比单图匹配宽松）的归为同簇
- 合并执行：每簇并入 命中次数最多 的成员（信息量最大的留下）

dry_run 模式只返回分簇预览（成员 + 簇内相似度），确认后再正式执行。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from core.config import get_config_int
from core.log import log

from . import matcher
from .store import FaceStore
from .vectors import pairwise_sims

_LOG_TAG = "人脸"


def insignificant_max_matches() -> int:
    """低价值人物判定线：最大命中次数。

    命中少的临时人物通常是群聊路人/背景人脸，无关紧要。
    """
    return get_config_int("face_insignificant_max_matches", 2)


async def _anchor_similarity(
    store: FaceStore, *, status: str,
) -> tuple[List[int], Dict[int, Dict[str, Any]], np.ndarray]:
    """按状态取参与聚类的人物 id、档案映射与锚两两余弦矩阵。

    无锚的档案（从未有合格样本）不参与聚类。
    """
    listing = await store.list_persons(status=status, limit=500)
    persons = {int(p["id"]): p for p in listing["items"]}
    ids, matrix = await store.person_anchor_matrix()
    pos_of = {pid: i for i, pid in enumerate(ids)}
    keep = [pid for pid in ids if pid in persons]
    if not keep:
        return [], persons, np.zeros((0, 0))
    sub = matrix[[pos_of[pid] for pid in keep]]
    return keep, persons, pairwise_sims(sub)


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
    store: FaceStore,
    *,
    threshold: Optional[float] = None,
    status: str = "pending",
) -> List[Dict[str, Any]]:
    """按锚相似度聚类，返回建议合并的簇（成员数 ≥2）。

    每簇：{"members": [{person 简报 + similarity}], "keep_id", "best_similarity"}
    """
    threshold = threshold if threshold is not None else matcher.merge_threshold()
    ids, persons, sims = await _anchor_similarity(store, status=status)

    uf = _UnionFind(ids)
    best_sim: Dict[int, float] = {i: 0.0 for i in ids}
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            sim = float(sims[x, y])
            if sim >= threshold:
                uf.union(ids[x], ids[y])
                best_sim[ids[x]] = max(best_sim[ids[x]], sim)
                best_sim[ids[y]] = max(best_sim[ids[y]], sim)

    groups: Dict[int, List[int]] = {}
    for i in ids:
        groups.setdefault(uf.find(i), []).append(i)

    clusters: List[Dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: persons[i]["match_count"], reverse=True)
        clusters.append({
            "members": [
                {
                    "id": i,
                    "person_key": persons[i]["person_key"],
                    "name": persons[i]["name"],
                    "match_count": persons[i]["match_count"],
                    "similarity": round(best_sim[i], 4),
                }
                for i in members
            ],
            "keep_id": members[0],  # 命中最多者保留
            "best_similarity": round(max(best_sim[i] for i in members), 4),
        })
    clusters.sort(key=lambda c: c["best_similarity"], reverse=True)
    return clusters


async def find_insignificant(
    store: FaceStore,
    *,
    max_matches: Optional[int] = None,
    exclude_ids: Optional[set[int]] = None,
) -> List[Dict[str, Any]]:
    """找出低价值临时人物：命中少（群聊路人/背景人脸）。"""
    max_matches = (max_matches if max_matches is not None
                   else insignificant_max_matches())
    exclude_ids = exclude_ids or set()
    listing = await store.list_persons(status="pending", limit=500)
    result: List[Dict[str, Any]] = []
    for person in listing["items"]:
        if int(person["id"]) in exclude_ids:
            continue
        if person["match_count"] <= max_matches:
            result.append({
                "id": person["id"],
                "person_key": person["person_key"],
                "name": person["name"],
                "match_count": person["match_count"],
            })
    return result


async def consolidate(
    store: FaceStore,
    *,
    threshold: Optional[float] = None,
    dry_run: bool = True,
    status: str = "pending",
    prune_insignificant: bool = False,
) -> Dict[str, Any]:
    """相似度合并整理 + 低价值清理。

    合并：每簇并入命中次数最多的成员（dry_run=True 只预览）。
    清理：合并后仍低价值的临时人物（命中少，多为路人），
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
                        "from": member["person_key"],
                        "into": result["target"]["person_key"] if result["target"] else "",
                        "samples_moved": result["samples_moved"],
                    })
                except ValueError as exc:
                    log(f"合并跳过 [{member['person_key']}]: {exc}",
                        "WARNING", tag=_LOG_TAG)

    # 低价值候选（执行合并后重新评估）。合并涉及的所有人都必须排除：
    # 保留者若被误清，刚并入的样本会随删除级联丢失
    if not dry_run:
        cluster_ids = {int(m["id"]) for c in clusters for m in c["members"]}
    else:
        cluster_ids = set()
    insignificant = await find_insignificant(store, exclude_ids=cluster_ids)
    pruned: List[Dict[str, Any]] = []
    if prune_insignificant and not dry_run and insignificant:
        for person in insignificant:
            deleted = await store.delete_person(int(person["id"]))
            if deleted:
                pruned.append(person)

    return {
        "dry_run": dry_run,
        "threshold": threshold if threshold is not None else matcher.merge_threshold(),
        "clusters": clusters,
        "cluster_count": len(clusters),
        "persons_affected": sum(len(c["members"]) for c in clusters),
        "merges": merges,
        "insignificant": insignificant,
        "insignificant_limits": {"max_matches": insignificant_max_matches()},
        "pruned": pruned,
    }
