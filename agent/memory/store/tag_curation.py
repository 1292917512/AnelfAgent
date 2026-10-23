"""标签治理豁免：归并候选的裁决结论落库（登记后议程不再生产该候选）。

事实归系统、决策归 AI 的闭环最后一环：候选由 TagIntelligence 确定性
生产，AI 判定误报/设计使然的候选对经 tag_curation_exempt 工具登记豁免，
curate_candidates 塑形时剔除——常驻误报不再逐拍重现。签名由系统产出
随候选下发（exempt_signature 字段），AI 处置时原样回传，口径零歧义。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from .connection import MemoryConnectionManager
from .tag_intel import normalize_tag_form

EXEMPTION_KINDS = frozenset(("form_variant", "containment"))


def exemption_signature(kind: str, candidate: Dict[str, Any]) -> str:
    """候选对的豁免签名：form_variant=归一形态；containment=排序形态对。"""
    if kind == "form_variant":
        return normalize_tag_form(str(candidate.get("into", "")))
    return "|".join(
        sorted(
            (
                str(candidate.get("from", "")),
                str(candidate.get("into", "")),
            )
        )
    )


async def curate_candidates(
    raw: List[Dict[str, Any]],
    conn: MemoryConnectionManager,
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """豁免过滤 + 签名下发；豁免层读取失败时原样放行（议程不因此停产）。"""
    try:
        exemptions = await list_exemptions(conn)
    except Exception:
        exemptions = []
    exempted = {(str(e["kind"]), str(e["signature"])) for e in exemptions}
    out: List[Dict[str, Any]] = []
    for candidate in raw:
        kind = str(candidate.get("kind", ""))
        signature = exemption_signature(kind, candidate)
        if (kind, signature) in exempted:
            continue
        out.append({**candidate, "exempt_signature": signature})
        if len(out) >= limit:
            break
    return out


async def set_exemption(
    conn: MemoryConnectionManager,
    kind: str,
    signature: str,
    *,
    reason: str = "",
    actor: str = "",
) -> Dict[str, Any]:
    """登记豁免（同 kind+signature 重复登记更新理由并复活）。"""
    if kind not in EXEMPTION_KINDS:
        raise ValueError(f"未知豁免类型: {kind}（可选 {sorted(EXEMPTION_KINDS)}）")
    signature = (signature or "").strip()
    if not signature:
        raise ValueError("豁免签名不能为空")
    db = await conn.get_db()
    async with conn.tx(db):
        await db.execute(
            "INSERT INTO tag_curation_exemptions "
            "(kind, signature, reason, actor, active, created_ns, revoked_ns) "
            "VALUES (?, ?, ?, ?, 1, ?, 0) "
            "ON CONFLICT(kind, signature) DO UPDATE SET "
            "reason=excluded.reason, actor=excluded.actor, active=1, revoked_ns=0",
            (kind, signature, (reason or "")[:200], (actor or "")[:40], time.time_ns()),
        )
    return {"kind": kind, "signature": signature, "active": True}


async def revoke_exemption(
    conn: MemoryConnectionManager,
    kind: str,
    signature: str,
) -> bool:
    """撤销豁免（标记失效，议程恢复生产该候选）。"""
    db = await conn.get_db()
    async with conn.tx(db):
        cursor = await db.execute(
            "UPDATE tag_curation_exemptions SET active=0, revoked_ns=? WHERE kind=? AND signature=? AND active=1",
            (time.time_ns(), kind, (signature or "").strip()),
        )
    return (cursor.rowcount or 0) > 0


async def list_exemptions(
    conn: MemoryConnectionManager,
    *,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    """豁免清单（默认仅生效中）。"""
    db = await conn.get_db()
    where = "WHERE active=1" if active_only else ""
    cursor = await db.execute(
        "SELECT kind, signature, reason, actor, created_ns FROM tag_curation_exemptions "
        f"{where} ORDER BY id DESC LIMIT 200",
    )
    return [
        {
            "kind": str(r["kind"]),
            "signature": str(r["signature"]),
            "reason": str(r["reason"]),
            "actor": str(r["actor"]),
            "created": r["created_ns"] / 1e9,
        }
        for r in await cursor.fetchall()
    ]
