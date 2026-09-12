"""委托服务 -- 全局子代理运行总览与面板操作（Dashboard「子代理」面板数据源）。

Web 侧委托能力的唯一入口：全局运行快照（running_snapshot_all）/ 执行历史
（journal 账本折叠）/ 进度流读取 / 转向指令 / 取消。面板操作全部汇入
DelegationManager 既有闭环——steer 经 SteerInbox 双档投递回执给子代理 AI，
cancel 经注册表完成通知/工具结果归因回馈父 AI，不产生第二条反馈通道。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.log import log
from services._runtime import get_runtime

if TYPE_CHECKING:
    from agent.delegation.delegation_manager import DelegationManager


def _manager() -> Optional["DelegationManager"]:
    """解析委托管理器（runtime 未就绪返回 None）。"""
    rt = get_runtime()
    if rt is None:
        return None
    return getattr(rt.mind, "delegation_manager", None)


class DelegationService:

    def overview(self) -> Dict[str, Any]:
        """全 scope 运行中委托快照（含归属维度与实时进度）。"""
        dm = _manager()
        return {"running": dm.running_snapshot_all() if dm is not None else []}

    def running_for_scope(self, scope: str) -> List[Dict[str, Any]]:
        """指定 scope 运行中委托快照（聊天页卡片恢复用）。"""
        dm = _manager()
        return dm.running_snapshot(scope) if dm is not None else []

    def history(self, limit: int = 20) -> Dict[str, Any]:
        """近期委托执行历史（账本 started/closed 配对折叠，按结束时间倒序）。"""
        from agent.delegation import journal
        return {"items": journal.recent_history(limit)}

    def progress(self, delegation_id: str, tail: int = 200) -> Dict[str, Any]:
        """委托进度流尾部行 + 运行状态（面板进度查看，轮询数据源）。"""
        from agent.delegation import journal
        result = journal.read_progress_tail(delegation_id, tail)
        result["delegation_id"] = delegation_id
        dm = _manager()
        result["running"] = dm.is_running(delegation_id) if dm is not None else False
        return result

    def steer(self, delegation_id: str, message: str, mode: str = "steer") -> Dict[str, Any]:
        """面板转向指令：标注来源后经 manager 双档投递（回执给子代理 AI）。"""
        dm = _manager()
        if dm is None:
            return {"error": "runtime 未就绪"}
        text = (message or "").strip()
        if not text:
            return {"error": "message 不能为空"}
        result = dm.steer(delegation_id, f"（来自 Web 面板的指令）{text}", mode)
        if result.get("ok"):
            log(f"Web 面板转向指令已投递 ({mode}): {delegation_id} msg={text[:60]}", tag="委托")
        return result

    def cancel(self, delegation_id: str) -> Optional[bool]:
        """面板取消委托（反馈经注册表完成通知/工具结果归因回馈父 AI）。

        Returns:
            True/False 表示取消是否受理；None 表示 runtime 未就绪。
        """
        dm = _manager()
        if dm is None:
            return None
        ok = dm.cancel(delegation_id)
        if ok:
            log(f"Web 面板取消委托: {delegation_id}", tag="委托")
        return ok
