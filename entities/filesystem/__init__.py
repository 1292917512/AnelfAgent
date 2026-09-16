"""filesystem 实体 — 工具面在 tools.py 自注册；此处挂进程级生命周期。"""

from __future__ import annotations


def register_lifecycle() -> None:
    """后台子进程看护入 Lifecycle：启动清扫孤儿进程组，关停终止在册子进程。

    清扫含 SIGTERM→宽限→SIGKILL 的阻塞轮询（每个孤儿最长数秒），
    经 to_thread 投递，不阻塞事件循环。
    """
    import asyncio

    from core.lifecycle import Lifecycle

    from .child_guard import sweep_stale_children, terminate_all_children

    async def _sweep() -> None:
        await asyncio.to_thread(sweep_stale_children)

    async def _terminate() -> None:
        await asyncio.to_thread(terminate_all_children)

    Lifecycle.register(
        "shell_child_guard", None,
        on_start=_sweep,
        cleanup=_terminate,
    )
