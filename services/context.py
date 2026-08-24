"""上下文观测服务 -- 上下文快照捕获/持久化与上下文层注册表。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ContextService:
    """上下文观测服务（Web 侧入口，封装 agent.mind 的上下文快照与管线元数据）。"""

    @staticmethod
    def _snapshot() -> Any:
        from agent.mind.context_snapshot import context_snapshot
        return context_snapshot

    # ------------------------------------------------------------------
    # 快照捕获（实时）
    # ------------------------------------------------------------------

    async def arm(self) -> None:
        """布防：等待下一次 LLM 调用时捕获完整上下文。"""
        await self._snapshot().arm()

    async def disarm(self) -> None:
        """取消布防。"""
        await self._snapshot().disarm()

    def get_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取当前内存快照（含分类后的 sections）。"""
        return self._snapshot().get()

    def get_status(self) -> Dict[str, Any]:
        """获取快照捕获状态。"""
        return self._snapshot().get_status()

    def clear(self) -> None:
        """清除当前内存快照 + 解除布防。"""
        self._snapshot().clear()

    def set_continuous(self, enabled: bool) -> None:
        """开关连续捕获模式（开启后每次 LLM 调用都捕获快照）。"""
        self._snapshot().set_continuous(enabled)

    def list_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取最近的连续捕获紧凑记录（分层统计 + 缓存观测）。"""
        return self._snapshot().list_records(limit)

    # ------------------------------------------------------------------
    # 快照持久化（历史）
    # ------------------------------------------------------------------

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """列出所有已保存的快照。"""
        return self._snapshot().list_snapshots()

    def load_snapshot(self, filename: str) -> Optional[Dict[str, Any]]:
        """获取指定快照的完整内容（不存在返回 None）。"""
        return self._snapshot().load_snapshot(filename)

    def delete_snapshot(self, filename: str) -> bool:
        """删除指定快照文件，返回是否删除成功。"""
        return self._snapshot().delete_snapshot(filename)

    def clear_all_snapshots(self) -> int:
        """清空所有已保存的快照，返回清除数量。"""
        return self._snapshot().clear_all_snapshots()

    # ------------------------------------------------------------------
    # 上下文层注册表
    # ------------------------------------------------------------------

    @staticmethod
    def list_layer_metas() -> List[Dict[str, Any]]:
        """上下文层注册表（变动率/展示名/构建责任方，序列化为前端结构）。"""
        from agent.mind.context_pipeline import list_layer_metas
        return [
            {
                "layer": m.layer,
                "volatility": m.volatility,
                "volatility_label": m.volatility_label,
                "label": m.label,
                "managed": m.managed,
            }
            for m in list_layer_metas()
        ]
