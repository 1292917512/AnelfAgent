"""后台任务注册表 — scope 级后台任务登记、完成路由与等待原语。

设计要点（参考 hermes process_registry，按对话场景裁剪）：
- 所有后台任务（子代理委托等）统一登记到本注册表，完成事件按 scope 归属路由。
- 完成事件有两条互斥的送达路径（恰好一次）：
  1. 轮内会合：think_loop 检测到等待意图时挂起，wait_any 将完成事件直接
     注入当前思考循环（AI 在同一轮内继续处理）；
  2. 轮外通知：无等待者时 complete() 返回未认领，由调用方触发新一轮
     REPLY（完成即新 turn），事件标记为已送达避免重复投递。
- 等待是协作式的：1 秒粒度轮询，可响应中断信号与新消息到达（由调用方
  通过 should_abort 传入），不阻塞事件循环。
- 进程内实现：后台任务本身是 asyncio.Task，进程退出即失效，无持久化需求。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from core.log import log

# 等待循环的轮询粒度（秒）：平衡事件响应延迟与空转开销
_POLL_INTERVAL = 1.0
# 每个 scope 保留的已完成记录上限（供 check 工具回看，超出丢弃最旧的）
_MAX_COMPLETED_PER_SCOPE = 20


@dataclass
class BackgroundTaskInfo:
    """运行中的后台任务描述。"""

    task_id: str
    scope: str
    kind: str
    description: str
    started_at: float

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at


@dataclass
class TaskCompletion:
    """后台任务的完成结果。"""

    task_id: str
    kind: str
    description: str
    success: bool
    summary: str
    finished_at: float


@dataclass
class _TaskRecord:
    """注册表内部记录：任务描述 + 完成状态 + 送达标记。"""

    info: BackgroundTaskInfo
    done: bool = False
    success: bool = False
    summary: str = ""
    finished_at: float = 0.0
    # 已送达：完成事件已通过轮内注入或轮外通知送达 AI，wait_any 不再重复返回
    delivered: bool = False

    def to_completion(self) -> TaskCompletion:
        return TaskCompletion(
            task_id=self.info.task_id,
            kind=self.info.kind,
            description=self.info.description,
            success=self.success,
            summary=self.summary,
            finished_at=self.finished_at,
        )


@dataclass
class WaitResult:
    """wait_any 的等待结果。

    reason: completed（有任务完成）/ timeout（超时）/ interrupted（被外部信号打断）
    """

    reason: str
    completions: List[TaskCompletion] = field(default_factory=list)


class BackgroundTaskRegistry:
    """scope 级后台任务注册表（进程内，无持久化需求）。"""

    def __init__(self) -> None:
        self._records: Dict[str, _TaskRecord] = {}
        self._events: Dict[str, asyncio.Event] = {}
        # scope -> 正在 wait_any 中挂起的等待者数量（轮内会合判定依据）
        self._waiting: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # 登记与完成
    # ------------------------------------------------------------------

    def register(self, scope: str, kind: str, description: str) -> str:
        """登记一个后台任务，返回任务 ID。"""
        task_id = uuid.uuid4().hex[:8]
        self._records[task_id] = _TaskRecord(
            info=BackgroundTaskInfo(
                task_id=task_id,
                scope=scope or "_global",
                kind=kind,
                description=description,
                started_at=time.time(),
            ),
        )
        self._purge_completed(scope or "_global")
        log(f"后台任务已登记: {task_id} [{kind}] {description[:60]}", tag="后台")
        return task_id

    def complete(self, task_id: str, success: bool, summary: str) -> bool:
        """标记任务完成并唤醒等待者。

        Returns:
            True 表示该 scope 存在轮内等待者（完成事件由 wait_any 送达）；
            False 表示无等待者，调用方应走轮外通知（事件已标记为已送达，
            避免后续 wait_any 重复投递）。
        """
        rec = self._records.get(task_id)
        if rec is None or rec.done:
            return True
        rec.done = True
        rec.success = success
        rec.summary = summary
        rec.finished_at = time.time()

        claimed = self._waiting.get(rec.info.scope, 0) > 0
        if not claimed:
            rec.delivered = True
        event = self._events.get(rec.info.scope)
        if event is not None:
            event.set()
        status = "成功" if success else "失败"
        log(
            f"后台任务完成: {task_id} ({status}) "
            f"{'轮内会合' if claimed else '轮外通知'}",
            tag="后台",
        )
        return claimed

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def running(self, scope: str) -> List[BackgroundTaskInfo]:
        """该 scope 下仍在运行的任务列表。"""
        return [
            rec.info for rec in self._records.values()
            if rec.info.scope == scope and not rec.done
        ]

    def completed(self, scope: str) -> List[TaskCompletion]:
        """该 scope 下已完成的任务结果（含已送达，供主动查询回看）。"""
        return [
            rec.to_completion() for rec in self._records.values()
            if rec.info.scope == scope and rec.done
        ]

    def snapshot(self, scope: str) -> Dict:
        """运行中 + 已完成的完整状态快照（check_background_tasks 工具用）。"""
        return {
            "running": [
                {
                    "task_id": t.task_id,
                    "kind": t.kind,
                    "description": t.description,
                    "elapsed_seconds": int(t.elapsed),
                }
                for t in self.running(scope)
            ],
            "completed": [
                {
                    "task_id": c.task_id,
                    "kind": c.kind,
                    "description": c.description,
                    "success": c.success,
                    "summary": c.summary,
                }
                for c in self.completed(scope)
            ],
        }

    # ------------------------------------------------------------------
    # 等待原语
    # ------------------------------------------------------------------

    async def wait_any(
            self,
            scope: str,
            timeout: float,
            should_abort: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> WaitResult:
        """挂起等待该 scope 任一后台任务完成。

        1 秒粒度轮询，每轮检查 should_abort（中断信号/新消息到达），
        超时或被打断时安全返回，不消费任何完成事件。

        Args:
            scope: 对话 scope
            timeout: 等待上限（秒）
            should_abort: 可选的中止判定（返回 True 立即以 interrupted 结束）
        """
        event = self._events.setdefault(scope, asyncio.Event())
        self._waiting[scope] = self._waiting.get(scope, 0) + 1
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            while True:
                fresh = self._collect_undelivered(scope)
                if fresh:
                    for rec in fresh:
                        rec.delivered = True
                    return WaitResult(
                        reason="completed",
                        completions=[rec.to_completion() for rec in fresh],
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return WaitResult(reason="timeout")
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=min(_POLL_INTERVAL, remaining))
                except asyncio.TimeoutError:
                    pass
                if should_abort is not None and await should_abort():
                    return WaitResult(reason="interrupted")
        finally:
            self._waiting[scope] = max(0, self._waiting.get(scope, 1) - 1)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _collect_undelivered(self, scope: str) -> List[_TaskRecord]:
        return [
            rec for rec in self._records.values()
            if rec.info.scope == scope and rec.done and not rec.delivered
        ]

    def _purge_completed(self, scope: str) -> None:
        """已完成记录超出上限时丢弃最旧的（只影响历史回看，不影响运行态）。"""
        done = [
            rec for rec in self._records.values()
            if rec.info.scope == scope and rec.done
        ]
        if len(done) <= _MAX_COMPLETED_PER_SCOPE:
            return
        done.sort(key=lambda rec: rec.finished_at)
        for rec in done[: len(done) - _MAX_COMPLETED_PER_SCOPE]:
            self._records.pop(rec.info.task_id, None)
