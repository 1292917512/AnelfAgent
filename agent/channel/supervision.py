"""频道看门狗 — ERROR 状态频道的自动恢复。

设计边界（明确不做什么）：
- 只监督"自报告"的 ERROR 状态：适配器内部捕获异常后置 ERROR 的频道
  由本模块自动重启；内部任务静默死亡但 _status 仍为 RUNNING 的频道
  不在监督范围（那需要适配器级健康探针，属于各适配器自身职责）。
- 不重启 STOPPED：主动停止（stop_channel / stop_all / 关停流程）是用户意图，
  看门狗绝不违背。
- 防打爆：单频道连续重启失败 max_restarts 次后标记 degraded 并停手，
  等待人工介入（重载配置/重启进程），避免对故障平台造成重连风暴。

恢复策略：指数退避 base_backoff * 2^n，封顶 max_backoff；
频道回到 RUNNING 即重置失败计数。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from core.log import log

from .base import BaseChannel, ChannelStatus


class ChannelSupervisor:
    """ERROR 频道看门狗：周期巡检 + 指数退避重启 + 连挂降级。"""

    def __init__(
            self,
            manager: Any,
            *,
            interval: float = 10.0,
            base_backoff: float = 2.0,
            max_backoff: float = 60.0,
            max_restarts: int = 5,
    ) -> None:
        self._manager = manager
        self._interval = interval
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._max_restarts = max_restarts
        # channel_id → 连续重启失败次数
        self._fail_counts: Dict[str, int] = {}
        # 已降级（连挂停手）的频道
        self._degraded: set[str] = set()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动后台巡检（幂等）。"""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._monitor_loop(), name="agent.channel.supervisor",
        )
        log(
            f"频道看门狗已启动: 巡检 {self._interval}s, 连挂上限 {self._max_restarts} 次",
            tag="看门狗",
        )

    async def stop(self) -> None:
        """停止巡检（进程关停时调用）。"""
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    # ------------------------------------------------------------------
    # 巡检主循环
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._interval)
            try:
                await self._inspect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 看门狗自身绝不能崩溃 —— 它是最后的防线
                log(f"看门狗巡检异常（已吞没）: {exc}", "WARNING", tag="看门狗")

    async def _inspect_once(self) -> None:
        for cid, channel in self._manager.list_channels().items():
            status = getattr(channel, "_status", None)
            if status == ChannelStatus.RUNNING:
                # 健康频道：重置失败计数与降级标记
                self._fail_counts.pop(cid, None)
                self._degraded.discard(cid)
                continue
            if status != ChannelStatus.ERROR:
                # STOPPED / STARTING / RECONNECTING 均不干预
                # （RECONNECTING 是适配器自愈中，看门狗不抢方向盘）
                continue
            if cid in self._degraded:
                continue
            await self._restart_with_backoff(cid, channel)

    async def _restart_with_backoff(self, cid: str, channel: BaseChannel) -> None:
        fails = self._fail_counts.get(cid, 0)
        if fails >= self._max_restarts:
            self._degraded.add(cid)
            log(
                f"频道连挂 {fails} 次，已降级停手等待人工介入: {cid}",
                "ERROR", tag="看门狗",
            )
            return

        backoff = min(self._base_backoff * (2 ** fails), self._max_backoff)
        log(
            f"频道处于 ERROR，{backoff:.0f}s 后尝试重启 (第 {fails + 1} 次): {cid}",
            "WARNING", tag="看门狗",
        )
        await asyncio.sleep(backoff)
        if self._stopping:
            return
        # 退避期间状态可能已被其他路径改变（人工重启/适配器自愈），重新确认
        if getattr(channel, "_status", None) != ChannelStatus.ERROR:
            return

        ok = await self._manager.start_channel(cid)
        if ok:
            self._fail_counts.pop(cid, None)
            log(f"频道看门狗重启成功: {cid}", tag="看门狗")
        else:
            self._fail_counts[cid] = fails + 1
            log(
                f"频道看门狗重启失败 ({fails + 1}/{self._max_restarts}): {cid}",
                "WARNING", tag="看门狗",
            )

    # ------------------------------------------------------------------
    # 可观测性
    # ------------------------------------------------------------------

    def get_status_info(self) -> Dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "fail_counts": dict(self._fail_counts),
            "degraded": sorted(self._degraded),
        }


# ------------------------------------------------------------------
# 全局单例
# ------------------------------------------------------------------

_supervisor: Optional[ChannelSupervisor] = None


def get_channel_supervisor() -> Optional[ChannelSupervisor]:
    """取看门狗实例（未启动时返回 None）。"""
    return _supervisor


def start_channel_supervisor(manager: Any) -> ChannelSupervisor:
    """创建并启动看门狗（launch 流程调用，幂等）。"""
    global _supervisor
    if _supervisor is None:
        from core.config import get_config_bool, get_config_float, get_config_int
        _supervisor = ChannelSupervisor(
            manager,
            interval=get_config_float("channel_supervisor_interval", 10.0),
            max_restarts=get_config_int("channel_supervisor_max_restarts", 5),
        )
    _supervisor.start()
    return _supervisor


async def stop_channel_supervisor() -> None:
    """停止看门狗（关停流程调用）。"""
    global _supervisor
    if _supervisor is not None:
        await _supervisor.stop()


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_SUPERVISOR_CONFIGS = {
    "频道看门狗": {
        "channel_supervisor_enabled": {
            "description": "是否启用频道看门狗（ERROR 频道自动退避重启）",
            "default": True,
        },
        "channel_supervisor_interval": {
            "description": "看门狗巡检间隔（秒）",
            "default": 10.0,
        },
        "channel_supervisor_max_restarts": {
            "description": "单频道连续重启失败上限（超过则降级停手）",
            "default": 5,
        },
    },
}

from core.config import get_config_bool, register_configs_safe  # noqa: E402

register_configs_safe(_SUPERVISOR_CONFIGS)


def is_supervisor_enabled() -> bool:
    """看门狗总开关。"""
    return get_config_bool("channel_supervisor_enabled", True)
