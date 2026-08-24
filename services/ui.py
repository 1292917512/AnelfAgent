"""界面交互服务 -- ui_ask 应答与工作台状态快照（封装 entities.ui）。"""

from __future__ import annotations

from typing import Any, Dict


class UiService:
    """界面交互服务（Web 侧入口）。"""

    @staticmethod
    def resolve_ask(ask_id: str, answer: str) -> bool:
        """提交 ui_ask 弹窗的回答，解决后端挂起的提问。"""
        from entities.ui.tools import resolve_ask
        return resolve_ask(ask_id, answer)

    @staticmethod
    def update_ui_state(state: Dict[str, Any]) -> None:
        """更新前端上报的工作台状态快照（供 ui_get_state 工具查询）。"""
        from entities.ui.tools import update_ui_state
        update_ui_state(state)

    @staticmethod
    def get_ui_state_snapshot() -> Dict[str, Any]:
        """返回当前工作台状态快照。"""
        from entities.ui.tools import get_ui_state_snapshot
        return get_ui_state_snapshot()
