"""SillyTavern 运行状态动态上下文注入。

酒馆运行时注入简明状态（URL、版本、角色数、最近聊天），
关闭时返回 None → 零注入。对齐 channels/bilibili/context.py 的
官方模式：on_start 起后台轮询采集，provide() 零 I/O 读快照
（ContextProviderRegistry 对 provide() 有 1s 超时硬约束）。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from core.context_provider import ProviderSnapshot
from core.log import log
from entities._sdk import context_provider

from . import service
from . import config as st_config

_POLL_INTERVAL = 30.0  # 秒
_snapshot: dict = {"status": None, "character_count": None, "recent": []}


async def _collect_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_collect_once)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # fail-open：探测异常只记日志
            log(f"酒馆状态采集异常: {e}", "WARNING", tag="酒馆")
        await asyncio.sleep(_POLL_INTERVAL)


def _collect_once() -> None:
    st = service.status()
    _snapshot["status"] = st
    if not st.get("running"):
        _snapshot["character_count"] = None
        _snapshot["recent"] = []
        return
    # 顺带采集轻量元数据（在 to_thread 中执行，不受 provide 超时约束）
    from .st_client import STError, get_st_client
    base = st_config.base_url()
    client = get_st_client()
    try:
        chars = client.characters_all(base)
        _snapshot["character_count"] = len(chars)
        top = sorted(
            chars, key=lambda c: float(c.get("talkativeness") or 0), reverse=True)[:3]
        _snapshot["recent"] = [c.get("name", "") for c in top if c.get("name")]
    except STError:
        _snapshot["character_count"] = None
        _snapshot["recent"] = []


@context_provider(name="sillytavern_status", priority=30, max_tokens=300, group="sillytavern")
class SillyTavernStatusProvider:
    """酒馆运行中注入状态卡片；关闭时零注入。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    async def on_start(self) -> None:
        await asyncio.to_thread(_collect_once)
        self._task = asyncio.create_task(_collect_loop())

    async def on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        cfg = st_config.load_config()
        if not cfg.get("context_inject", True):
            return None
        st = _snapshot.get("status")
        if not st or not st.get("running"):
            return None
        lines = [
            "[SillyTavern 酒馆] 运行中",
            f"- 地址: {st.get('url')} (pid={st.get('pid')}, 版本 {st.get('version') or '未知'})",
        ]
        if _snapshot.get("character_count") is not None:
            lines.append(f"- 角色数: {_snapshot['character_count']}")
        if _snapshot.get("recent"):
            lines.append(f"- 活跃角色: {', '.join(_snapshot['recent'])}")
        lines.append("可用工具: sillytavern_start/stop/restart/status、角色管理、"
                     "模型配置、sillytavern_update 等")
        return ProviderSnapshot(content="\n".join(lines), ready=True)


def register_context() -> None:
    """导入即注册（由 __init__.py 调用）。@context_provider 装饰器在类定义时
    已完成注册，此函数仅保留显式语义入口。"""
    return None
