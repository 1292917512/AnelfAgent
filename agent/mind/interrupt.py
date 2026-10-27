"""中断注册表 — 自主循环的协作式"刹车"。

自主性三定律第 2 条：每个循环必须可中断。

设计要点：
- 协作式而非抢占式：think_loop 在每轮 LLM 调用前检查一次信号，
  命中则安全收束（不发半截消息、不写残缺工具链、历史留中断元消息），
  而不是强行 cancel 协程留下不一致状态。
- 在途等待点可唤醒：request() 同时置位 scope 级 asyncio.Event，
  正在消费 LLM 流/执行工具批次的 await 点与该事件竞争，中断到达即
  干净撤离（关闭流、未开始的调用合成占位结果），收束仍走统一路径。
  事件随请求登记/消费而生灭——未消费的请求才有事件，消费即换新。
- scope 粒度：中断只影响目标对话，其他 scope 的回复/反思不受影响。
- 触发源：Web 控制、频道命令和内部调用通过 Mind.interrupt(scope) 请求中断；
  聊天正文不触发关键词中断。
"""
from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional

from core.cancellation import ExecutionSignal, execution_signal
from core.log import log


@dataclass
class InterruptRequest:
    """一条中断请求。"""

    reason: str
    requested_at: float


class InterruptRegistry:
    """scope 级中断信号注册表（进程内，无持久化需求）。"""

    def __init__(self) -> None:
        self._requests: Dict[str, InterruptRequest] = {}
        self._signals: dict[str, ExecutionSignal] = {}
        self._executions: dict[str, str] = {}

    @contextmanager
    def execution(self, run_id: str, scope: str) -> Iterator[None]:
        """绑定一次在途执行，退出即释放控制目标与未消费的信号。"""
        if run_id in self._executions or scope in self._executions.values():
            raise ValueError("执行标识或 scope 已在运行")
        self._executions[run_id] = scope
        signal = self.signal(scope)
        signal.run_id = run_id
        try:
            with execution_signal(signal):
                yield
        finally:
            self._executions.pop(run_id, None)
            self.clear(scope)

    def scope_for_run(self, run_id: str) -> str | None:
        """只返回仍在运行的精确控制目标，不依赖展示记录的保留期限。"""
        return self._executions.get(run_id)

    def is_active(self, scope: str) -> bool:
        """回复与隔离反思共用的可中断执行事实。"""
        return scope in self._executions.values()

    def request(self, scope: str, reason: str = "") -> None:
        """请求中断指定 scope 的进行中会话（幂等：重复请求只更新时间）。"""
        if not scope:
            return
        self._requests[scope] = InterruptRequest(reason=reason, requested_at=time.time())
        # 事件登记与请求同生命周期：等待方经 event() 拿到的实例在请求被
        # 消费前保持可唤醒；重复请求只更新时间不重置事件
        self.signal(scope).request(reason)
        log(f"中断请求已登记: scope={scope} reason={reason or '未说明'}", tag="中断")

    def event(self, scope: str) -> asyncio.Event:
        """该 scope 的中断唤醒事件（登记/消费之间保持同一实例）。

        无未消费请求时返回未置位的新事件——等待方随后等待的是
        "下一次中断"，与请求语义一致。
        """
        return self.signal(scope).event

    def signal(self, scope: str) -> ExecutionSignal:
        """SDK 与思维循环共享同一信号，不另建取消通道。"""
        if scope not in self._signals:
            self._signals[scope] = ExecutionSignal(scope)
        return self._signals[scope]

    def is_requested(self, scope: str) -> bool:
        """该 scope 是否存在未消费的中断请求。"""
        return scope in self._requests

    def consume(self, scope: str) -> Optional[str]:
        """消费中断请求（收束时调用），返回原因；无请求返回 None。

        事件随之移除：下一次 event() 返回全新未置位实例，残留的置位
        状态不会误杀下一轮回复的在途等待。
        """
        self._signals.pop(scope, None)
        req = self._requests.pop(scope, None)
        return req.reason if req else None

    def clear(self, scope: str) -> None:
        """会话开始时清理历史请求，避免旧信号误杀新会话。"""
        self._requests.pop(scope, None)
        self._signals.pop(scope, None)

    def clear_before(self, scope: str, cutoff: float) -> None:
        """只清理 cutoff 之前的历史请求（保留回复启动窗口内的新中断）。

        会话启动清旧信号用：scope 登记活跃（_active_scopes.add）到
        think_session 清理之间有数百 ms 上下文构建窗口，期间用户发的
        "停止"是对本次回复的合法中断，不能被当作遗留清掉。
        """
        req = self._requests.get(scope)
        if req is not None and req.requested_at < cutoff:
            self._requests.pop(scope, None)
            self._signals.pop(scope, None)

    def pending_scopes(self) -> List[str]:
        """当前存在未消费请求的 scope 列表（可观测性用）。"""
        return list(self._requests)
