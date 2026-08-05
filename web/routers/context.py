"""上下文管理 API 路由 — 快照捕获/持久化 + 上下文提供者状态。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.mind.context_snapshot import context_snapshot

router = APIRouter(prefix="/context", tags=["context"])


class _ContinuousToggle(BaseModel):
    enabled: bool


# ------------------------------------------------------------------
# 快照捕获（实时）
# ------------------------------------------------------------------


@router.post("/snapshot/arm")
async def arm_snapshot() -> Dict[str, Any]:
    """布防：等待下一次 LLM 调用时捕获完整上下文。"""
    await context_snapshot.arm()
    return {"armed": True}


@router.post("/snapshot/disarm")
async def disarm_snapshot() -> Dict[str, Any]:
    """取消布防。"""
    await context_snapshot.disarm()
    return {"armed": False}


@router.get("/snapshot")
async def get_snapshot() -> Dict[str, Any]:
    """获取当前内存快照（含分类后的 sections）。"""
    snapshot = context_snapshot.get()
    return {
        "status": context_snapshot.get_status(),
        "snapshot": snapshot,
    }


@router.post("/snapshot/clear")
async def clear_snapshot() -> Dict[str, Any]:
    """清除当前内存快照 + 解除布防。"""
    context_snapshot.clear()
    return {"armed": False, "has_snapshot": False}


@router.put("/snapshot/continuous")
async def set_continuous(body: _ContinuousToggle) -> Dict[str, Any]:
    """开关连续捕获模式：开启后每次 LLM 调用都捕获快照（性能考虑，调试用）。"""
    context_snapshot.set_continuous(body.enabled)
    return {"continuous": body.enabled}


@router.get("/snapshot/records")
async def list_snapshot_records(limit: int = 100) -> Dict[str, Any]:
    """读取最近的连续捕获紧凑记录（分层统计 + 缓存观测，供外部调试工具轮询）。"""
    records = context_snapshot.list_records(limit)
    return {"records": records, "count": len(records)}


# ------------------------------------------------------------------
# 快照持久化（历史）
# ------------------------------------------------------------------


@router.get("/snapshots")
async def list_snapshots() -> Dict[str, Any]:
    """列出所有已保存的快照。"""
    snapshots = context_snapshot.list_snapshots()
    return {"snapshots": snapshots, "count": len(snapshots)}


@router.get("/snapshots/{filename}")
async def get_saved_snapshot(filename: str) -> Dict[str, Any]:
    """获取指定快照的完整内容。"""
    data = context_snapshot.load_snapshot(filename)
    if data is None:
        raise HTTPException(404, f"快照 '{filename}' 不存在")
    return data


@router.delete("/snapshots/{filename}")
async def delete_saved_snapshot(filename: str) -> Dict[str, Any]:
    """删除指定快照文件。"""
    ok = context_snapshot.delete_snapshot(filename)
    if not ok:
        raise HTTPException(404, f"快照 '{filename}' 不存在")
    return {"deleted": filename}


@router.post("/snapshots/clear")
async def clear_all_snapshots() -> Dict[str, Any]:
    """清空所有已保存的快照。"""
    count = context_snapshot.clear_all_snapshots()
    return {"cleared": count}


# ------------------------------------------------------------------
# 上下文提供者
# ------------------------------------------------------------------


@router.get("/providers")
async def get_context_providers() -> Dict[str, Any]:
    """上下文提供者状态（预算占用、峰值、每个 provider 指标）。"""
    from core.context_provider import ContextProviderRegistry
    return ContextProviderRegistry.get_status()
