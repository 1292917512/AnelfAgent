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
        _snapshot["model"] = None
        _snapshot["bridge_ok"] = None
        return
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
    # 当前模型配置（注入给 AI，让它知道酒馆在用哪个模型）
    try:
        oai = (client.get_settings(base)["settings"].get("oai_settings") or {})
        _snapshot["model"] = oai.get("openai_model") or None
    except STError:
        _snapshot["model"] = None
    # 桥接插件健康（AI 排障用）
    try:
        from . import chat_bridge
        _snapshot["bridge_ok"] = bool(chat_bridge.bridge_health().get("ok"))
    except Exception:
        _snapshot["bridge_ok"] = False


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
        if _snapshot.get("model"):
            lines.append(f"- 当前模型: {_snapshot['model']}")
        if _snapshot.get("character_count") is not None:
            lines.append(f"- 角色数: {_snapshot['character_count']}")
        if _snapshot.get("recent"):
            lines.append(f"- 活跃角色: {', '.join(_snapshot['recent'])}")
        bridge = _snapshot.get("bridge_ok")
        if bridge is False:
            lines.append("- 对话桥接: 不可用（anelf-bridge 插件未就绪，需重启酒馆）")
        lines.append("管理工具: sillytavern_overview 拿全貌、sillytavern_chat 对话、"
                     "角色/模型/源码管理；改了源码或模型后用 sillytavern_restart 生效")
        return ProviderSnapshot(content="\n".join(lines), ready=True)


def register_context() -> None:
    """导入即注册（由 __init__.py 调用）。@context_provider 装饰器在类定义时
    已完成注册，此函数仅保留显式语义入口。"""
    return None
