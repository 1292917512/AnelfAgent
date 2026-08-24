"""上下文管理 API 路由 — 快照捕获/持久化 + 上下文提供者状态。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import ContextService

router = APIRouter(prefix="/context", tags=["context"])

_context_svc = ContextService()


class _ContinuousToggle(BaseModel):
    enabled: bool


# ------------------------------------------------------------------
# 快照捕获（实时）
# ------------------------------------------------------------------


@router.post("/snapshot/arm")
async def arm_snapshot() -> Dict[str, Any]:
    """布防：等待下一次 LLM 调用时捕获完整上下文。"""
    await _context_svc.arm()
    return {"armed": True}


@router.post("/snapshot/disarm")
async def disarm_snapshot() -> Dict[str, Any]:
    """取消布防。"""
    await _context_svc.disarm()
    return {"armed": False}


@router.get("/snapshot")
async def get_snapshot() -> Dict[str, Any]:
    """获取当前内存快照（含分类后的 sections）。"""
    return {
        "status": _context_svc.get_status(),
        "snapshot": _context_svc.get_snapshot(),
    }


@router.post("/snapshot/clear")
async def clear_snapshot() -> Dict[str, Any]:
    """清除当前内存快照 + 解除布防。"""
    _context_svc.clear()
    return {"armed": False, "has_snapshot": False}


@router.put("/snapshot/continuous")
async def set_continuous(body: _ContinuousToggle) -> Dict[str, Any]:
    """开关连续捕获模式：开启后每次 LLM 调用都捕获快照（性能考虑，调试用）。"""
    _context_svc.set_continuous(body.enabled)
    return {"continuous": body.enabled}


@router.get("/snapshot/records")
async def list_snapshot_records(limit: int = 100) -> Dict[str, Any]:
    """读取最近的连续捕获紧凑记录（分层统计 + 缓存观测，供外部调试工具轮询）。"""
    records = _context_svc.list_records(limit)
    return {"records": records, "count": len(records)}


# ------------------------------------------------------------------
# 快照持久化（历史）
# ------------------------------------------------------------------


@router.get("/snapshots")
async def list_snapshots() -> Dict[str, Any]:
    """列出所有已保存的快照。"""
    snapshots = _context_svc.list_snapshots()
    return {"snapshots": snapshots, "count": len(snapshots)}


@router.get("/snapshots/{filename}")
async def get_saved_snapshot(filename: str) -> Dict[str, Any]:
    """获取指定快照的完整内容。"""
    data = _context_svc.load_snapshot(filename)
    if data is None:
        raise HTTPException(404, f"快照 '{filename}' 不存在")
    return data


@router.delete("/snapshots/{filename}")
async def delete_saved_snapshot(filename: str) -> Dict[str, Any]:
    """删除指定快照文件。"""
    ok = _context_svc.delete_snapshot(filename)
    if not ok:
        raise HTTPException(404, f"快照 '{filename}' 不存在")
    return {"deleted": filename}


@router.post("/snapshots/clear")
async def clear_all_snapshots() -> Dict[str, Any]:
    """清空所有已保存的快照。"""
    count = _context_svc.clear_all_snapshots()
    return {"cleared": count}


# ------------------------------------------------------------------
# 上下文提供者
# ------------------------------------------------------------------


@router.get("/providers")
async def get_context_providers() -> Dict[str, Any]:
    """上下文提供者状态（预算占用、峰值、每个 provider 指标）。"""
    from core.context_provider import ContextProviderRegistry
    return ContextProviderRegistry.get_status()


@router.get("/layers")
async def list_context_layers() -> Dict[str, Any]:
    """上下文层注册表（变动率/展示名/构建责任方，快照分层与展示的单一数据源）。"""
    return {"layers": _context_svc.list_layer_metas()}
