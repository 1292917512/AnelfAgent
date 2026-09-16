"""filesystem 实体 — 工具面在 tools.py 自注册；此处挂进程级生命周期。"""

from __future__ import annotations


def register_lifecycle() -> None:
    """后台子进程看护入 Lifecycle：启动清扫孤儿进程组，关停终止在册子进程。"""
    from core.lifecycle import Lifecycle

    from .child_guard import sweep_stale_children, terminate_all_children

    Lifecycle.register(
        "shell_child_guard", None,
        on_start=sweep_stale_children,
        cleanup=terminate_all_children,
    )
