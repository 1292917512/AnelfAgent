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
import inspect
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

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
    # 预期时长（秒，0=未声明）：超出时向 AI 发送超时提醒（不终止任务，
    # 去留由 AI 决策——系统提供可见性，不替 AI 做决定）
    expected_seconds: float = 0.0

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
    # 任务输出文件（可选，如后台 shell 的日志）——增量读取游标的数据源
    output_file: Optional[str] = None
    # 终止句柄（可选）：AI 经 terminate_background_task 决策终止时调用，
    # 返回是否受理；终态仍由生产者的完成路径登记（终态通知照常送达）
    killer: Optional[Callable[[], bool]] = None

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
        # task_id -> 增量输出已消费的字节偏移（read_task_output 单游标）
        self._output_cursors: Dict[str, int] = {}
        # 主事件循环（bind_loop 绑定；工作线程完成任务时经 call_soon_threadsafe 回到循环）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 轮外完成回调（无等待者时触发，由 Mind 注册，避免 entities 层直接 import agent.mind）
        self._on_unclaimed: Optional[Callable[[str, str, str, bool], Any]] = None
        # 超时提醒回调（任务超过预期时长仍在运行时触发，不改变任务状态）
        self._on_alert: Optional[Callable[[str, str, str, str], Any]] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定主事件循环（Mind 初始化时调用）。"""
        self._loop = loop

    def set_unclaimed_callback(self, callback: Callable[[str, str, str, bool], Any]) -> None:
        """注册轮外完成回调（无等待者时触发新 REPLY 周期）。

        Args:
            callback: (scope, description, summary, success) -> None 或协程
                （协程在主循环上 ensure_future；_finish 总在主循环执行）。
                非 conversation scope（_global / reflect:*）也回调，由接收方
                决定兜底去向（全局短期记忆桶）——完成事实不因 scope 静默丢弃。
        """
        self._on_unclaimed = callback

    def set_alert_callback(self, callback: Callable[[str, str, str, str], Any]) -> None:
        """注册超时提醒回调（任务超过预期时长仍在运行时触发）。

        Args:
            callback: (scope, description, detail, task_id) -> None 或协程。
                提醒不改变任务状态、不终止任务——以报告形式向 AI 呈现进度
                事实，真假超时与去留由 AI 自行判断。
        """
        self._on_alert = callback

    # ------------------------------------------------------------------
    # 登记与完成
    # ------------------------------------------------------------------

    def register(self, scope: str, kind: str, description: str,
                 expected_seconds: float = 0.0) -> str:
        """登记一个后台任务，返回任务 ID。"""
        task_id = uuid.uuid4().hex[:8]
        self._records[task_id] = _TaskRecord(
            info=BackgroundTaskInfo(
                task_id=task_id,
                scope=scope or "_global",
                kind=kind,
                description=description,
                started_at=time.time(),
                expected_seconds=expected_seconds,
            ),
        )
        self._purge_completed(scope or "_global")
        log(f"后台任务已登记: {task_id} [{kind}] {description[:60]}", tag="后台")
        return task_id

    def attach_killer(self, task_id: str, killer: Callable[[], bool]) -> None:
        """为任务关联终止句柄（terminate 用；shell 进程组击杀 / 委托取消）。"""
        rec = self._records.get(task_id)
        if rec is not None and callable(killer):
            rec.killer = killer

    # ------------------------------------------------------------------
    # 增量输出读取（单游标消费型，对齐 dsh jobs readOutput）
    # ------------------------------------------------------------------
    # task_id -> 已消费的字节偏移。游标留在注册表侧（生产者只管写文件），
    # 单读者语义：一个任务只有一个模型读者，每次读取只返回自上次以来的增量，
    # 轮询长任务输出不再全量重发。进程内生命周期与任务一致（重启即新任务）。

    def attach_output_file(self, task_id: str, output_file: str) -> None:
        """为任务关联输出文件（启动方调用，read_task_output 的数据源）。"""
        rec = self._records.get(task_id)
        if rec is not None and output_file:
            rec.output_file = output_file

    @staticmethod
    def _cut_utf8(data: bytes, max_chars: int) -> "tuple[bytes, bool]":
        """按码点数在字节边界截断，返回 (截断字节, 是否截断)。

        逐码点步进（按 UTF-8 首字节推断序列长度），保证游标按字节精确推进、
        不在多字节字符中间切断。
        """
        count = 0
        i = 0
        n = len(data)
        while i < n:
            if count >= max_chars:
                return data[:i], True
            b = data[i]
            if b < 0x80:
                step = 1
            elif b & 0xE0 == 0xC0:
                step = 2
            elif b & 0xF0 == 0xE0:
                step = 3
            elif b & 0xF8 == 0xF0:
                step = 4
            else:
                step = 1  # 非法首字节按单字节消费（配合 errors=replace 语义）
            i += min(step, n - i)
            count += 1
        return data, False

    def read_task_output(self, scope: str, task_id: str, max_chars: int = 8000) -> Dict[str, Any]:
        """消费任务自上次读取以来的增量输出（单游标，读后即推进）。

        Returns:
            {"ok": True, "task_id", "delta", "consumed_bytes", "truncated", "done"} 或
            {"ok": False, "error"}（任务不存在 / 跨会话 / 无输出文件 / 读取失败）。
            delta 为空串表示暂无新输出（轮询正常态）。
        """
        rec = self._records.get(task_id)
        if rec is None:
            return {"ok": False, "error": f"任务不存在: {task_id}"}
        if rec.info.scope != (scope or "_global"):
            return {"ok": False, "error": f"任务 {task_id} 不属于当前会话"}
        if not rec.output_file:
            return {"ok": False, "error": f"任务 {task_id} 无关联输出文件（仅 shell 类任务支持增量读取）"}
        offset = self._output_cursors.get(task_id, 0)
        try:
            with open(rec.output_file, "rb") as f:
                f.seek(offset)
                chunk = f.read(max_chars * 4 + 16)
        except OSError as exc:
            return {"ok": False, "error": f"读取任务输出失败: {exc}"}
        # 回退尾部可能不完整的多字节序列（文件仍在写入时最后一字符可能被截半）
        safe = chunk
        while safe and (safe[-1] & 0xC0) == 0x80:
            safe = safe[:-1]
        consumed, truncated = self._cut_utf8(safe, max_chars)
        delta = consumed.decode("utf-8", errors="replace")
        self._output_cursors[task_id] = offset + len(consumed)
        return {
            "ok": True,
            "task_id": task_id,
            "delta": delta,
            "consumed_bytes": offset + len(consumed),
            "truncated": truncated,
            "done": rec.done,
            "hint": "delta 为本次新增输出（消费型读取，重复调用不会重发旧内容）；truncated=true 表示还有未读。" if delta else "暂无新输出，可稍后再查或等待完成通知。",
        }

    def complete(self, task_id: str, success: bool, summary: str) -> bool:
        """标记任务完成并唤醒等待者（线程安全）。

        主循环线程内直接完成；工作线程（如后台 shell 等待线程）经
        call_soon_threadsafe 回到主循环完成，此时返回值无意义（恒 True）。

        Returns:
            True 表示该 scope 存在轮内等待者（完成事件由 wait_any 送达）；
            False 表示无等待者，调用方应走轮外通知（事件已标记为已送达，
            避免后续 wait_any 重复投递）。
        """
        if (
            self._loop is not None
            and self._loop.is_running()
            and threading.current_thread() is not threading.main_thread()
        ):
            self._loop.call_soon_threadsafe(self._finish, task_id, success, summary)
            return True
        return self._finish(task_id, success, summary)

    def _finish(self, task_id: str, success: bool, summary: str) -> bool:
        """完成处理的实际实现（须运行在主循环线程）。"""
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
        # 轮外完成（无等待者）：触发回调（由 Mind 注册，写对话历史并排入
        # 回复队列触发新 REPLY；非 conversation scope 由回调侧全局桶兜底）。
        # 通知失败时恢复未送达标记——先标记后投递会让回调异常静默丢通知
        if not claimed and self._on_unclaimed is not None:
            result = self._on_unclaimed(
                rec.info.scope, rec.info.description, summary[:1500], success,
            )
            if inspect.iscoroutine(result):
                async def _guarded_notify() -> None:
                    try:
                        await result
                    except Exception as exc:
                        rec.delivered = False
                        log(f"后台任务完成通知失败: {task_id}: {exc}", "ERROR", tag="后台")
                asyncio.ensure_future(_guarded_notify())
        return claimed

    def alert_timeout(self, task_id: str, detail: str) -> None:
        """任务超过预期时长仍在运行：向 AI 发送提醒（不改变任务状态，线程安全）。

        超时是提醒不是击杀——长下载等慢任务由 AI 拿着进度自行决策去留，
        终止走 ``terminate``。
        """
        if (
            self._loop is not None
            and self._loop.is_running()
            and threading.current_thread() is not threading.main_thread()
        ):
            self._loop.call_soon_threadsafe(self._alert, task_id, detail)
        else:
            self._alert(task_id, detail)

    def _alert(self, task_id: str, detail: str) -> None:
        """超时提醒的实际实现（须运行在主循环线程；任务已结束时丢弃）。"""
        rec = self._records.get(task_id)
        if rec is None or rec.done or self._on_alert is None:
            return
        result = self._on_alert(rec.info.scope, rec.info.description, detail, task_id)
        if inspect.iscoroutine(result):
            asyncio.ensure_future(result)

    def terminate(self, scope: str, task_id: str) -> Dict[str, Any]:
        """终止一个运行中的后台任务（AI 决策调用；killer 阻塞时调用方应放线程池）。

        Returns:
            {"ok": True, "terminated": bool, ...} 或 {"ok": False, "error"}。
            终止只发信号：终态仍由生产者的完成路径登记，完成通知照常送达。
        """
        rec = self._records.get(task_id)
        if rec is None:
            return {"ok": False, "error": f"任务不存在: {task_id}"}
        if rec.info.scope != (scope or "_global"):
            return {"ok": False, "error": f"任务 {task_id} 不属于当前会话"}
        if rec.done:
            return {
                "ok": True, "terminated": False, "already_finished": True,
                "success": rec.success, "summary": rec.summary[:200],
            }
        if rec.killer is None:
            return {"ok": False, "error": f"任务 {task_id} 不支持终止（无终止句柄）"}
        try:
            terminated = bool(rec.killer())
        except Exception as exc:
            return {"ok": False, "error": f"终止失败: {exc}"}
        return {
            "ok": terminated, "terminated": terminated,
            "hint": "终止信号已发出，任务结束时你会收到完成通知。",
        }

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
                    # 预期时长（0=未声明）：超出即已/将收到超时提醒
                    "expected_seconds": int(t.expected_seconds),
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
                    pass  # 超时属正常等待结束（正常控制流，非异常）
                if should_abort is not None and await should_abort():
                    return WaitResult(reason="interrupted")
        finally:
            self._waiting[scope] = max(0, self._waiting.get(scope, 1) - 1)
            # scope 无等待者且无运行中任务时回收事件与计数，防按 scope 无限累积
            if self._waiting[scope] == 0 and not self.running(scope):
                self._waiting.pop(scope, None)
                if not self._collect_undelivered(scope):
                    self._events.pop(scope, None)

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
            self._output_cursors.pop(rec.info.task_id, None)
