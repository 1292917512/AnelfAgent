"""后台任务注册表 — scope 级后台任务登记、完成路由与等待原语。

设计要点：
- 所有后台任务（子代理委托等）统一登记到本注册表，完成事件按 scope 归属路由。
- 完成事件有两条互斥的送达路径（恰好一次）：
  1. 轮内会合：think_loop 检测到等待意图时挂起，wait_any 将完成事件直接
     注入当前思考循环（AI 在同一轮内继续处理）；
  2. 轮外通知：无等待者时 complete() 返回未认领，由调用方触发新一轮
     REPLY（完成即新 turn），事件标记为已送达避免重复投递。
- 等待是协作式的：1 秒粒度轮询，可响应中断信号与新消息到达（由调用方
  通过 should_abort 传入），不阻塞事件循环。
- 进程内实现：后台任务本身是 asyncio.Task，进程退出即失效，无持久化需求。

模型可见：任务 ID、状态及完成结果经工具回执或所属会话的内部通知送达。
Token：状态摘要有界，完整结果按 ID 查询；等待不调用模型轮询。
缓存：后台状态仅在动态区和工具结果中更新，不修改稳定前缀。
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional

from agent.mind.background.evidence import TaskEvidence
from core.activity import EVENT_BACKGROUND_TASK_UPDATED, current_activity_id
from core.event_bus import event_bus
from core.log import log
from core.tags import tag_label
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_results import extract_error_text

# 等待循环的轮询粒度（秒）：平衡事件响应延迟与空转开销
_POLL_INTERVAL = 1.0
# 每个 scope 保留的已完成记录上限（供 check 工具回看，超出丢弃最旧的）
_MAX_COMPLETED_PER_SCOPE = 20
current_background_task_id: ContextVar[str] = ContextVar("background_task_id", default="")


class CompletionNotification(str, Enum):
    """完成事实的 AI 通知策略；通知按会话归属路由。"""

    ALWAYS = "always"
    ON_RESULT = "on_result"
    ON_FAILURE = "on_failure"
    SILENT = "silent"

    def accepts(self, success: bool, has_output: bool) -> bool:
        return (self is self.ALWAYS or self is self.ON_RESULT and (not success or has_output)
                or self is self.ON_FAILURE and not success)


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
    notification: CompletionNotification = CompletionNotification.ALWAYS
    joinable: bool = True
    turn_id: str = ""

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
    notification: CompletionNotification = CompletionNotification.ALWAYS


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
    progress_reader: Callable[[], Dict[str, Any]] | None = None
    progress: Dict[str, Any] = field(default_factory=dict)
    evidence: TaskEvidence = field(default_factory=TaskEvidence)
    stall_timer: asyncio.TimerHandle | None = None
    alerted_progress_at: float | None = None

    def to_completion(self) -> TaskCompletion:
        return TaskCompletion(
            task_id=self.info.task_id,
            kind=self.info.kind,
            description=self.info.description,
            success=self.success,
            summary=self.summary,
            finished_at=self.finished_at,
            notification=self.info.notification,
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

    def __init__(self, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._records: Dict[str, _TaskRecord] = {}
        self._admission_lock = threading.RLock()
        self._events: Dict[str, asyncio.Event] = {}
        self._wait_targets: dict[str, list[str]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closing = False
        # task_id -> 增量输出已消费的字节偏移（read_task_output 单游标）
        self._output_cursors: Dict[str, int] = {}
        # 运行时注入主循环，工作线程完成任务时经 call_soon_threadsafe 回到循环。
        self._loop = loop
        # 运行时接入轮外完成回调；注册表不依赖思维宿主。
        self._on_unclaimed: Optional[Callable[[str, str, str, bool], Any]] = None
        # 超时提醒回调（任务超过预期时长仍在运行时触发，不改变任务状态）
        self._on_alert: Optional[Callable[[str, str, str, str], Any]] = None

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
                 expected_seconds: float = 0.0, *,
                 notification: CompletionNotification = CompletionNotification.ALWAYS,
                 joinable: bool = True) -> str:
        """登记一个后台任务，返回任务 ID。"""
        with self._admission_lock:
            if self._closing:
                raise RuntimeError("后台任务服务正在关闭")
            task_id = uuid.uuid4().hex[:8]
            self._records[task_id] = _TaskRecord(
                info=BackgroundTaskInfo(
                    task_id=task_id,
                    scope=scope or "_global",
                    kind=kind,
                    description=description,
                    started_at=time.time(),
                    expected_seconds=expected_seconds,
                    notification=notification,
                    joinable=joinable,
                    turn_id=current_activity_id.get(),
                ),
            )
            self._purge_completed(scope or "_global")
            log(f"后台任务已登记: {task_id} [{kind}] {description[:60]}", tag="后台")
            self._publish(task_id)
            return task_id

    def _track(self, operation: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
        """持有后台协程，关停时统一等待或取消。"""
        task = asyncio.create_task(operation, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def submit(self, scope: str, kind: str, description: str,
               operation: Callable[[], Awaitable[str]]) -> str:
        """提交返回 JSON 的工具操作，保留结果并按会话通知完成。"""
        if self._closing:
            raise RuntimeError("后台任务服务正在关闭")
        task_id = self.register(scope, kind, description)

        async def run() -> None:
            token = current_background_task_id.set(task_id)
            try:
                result = await operation()
                success = not extract_error_text(result)
                self.complete(task_id, success, result)
            except Exception as exc:
                self.complete(task_id, False, error_from_exception(exc, action=description))
            finally:
                current_background_task_id.reset(token)

        task = self._track(run(), name=f"background.{kind}.{task_id}")

        def finish_cancelled(done: asyncio.Task[Any]) -> None:
            if done.cancelled():
                self.complete(task_id, False, tool_error(
                    "后台任务已取消", cause=ErrorCause.STATE, retryable=False, cancelled=True,
                ))

        task.add_done_callback(finish_cancelled)
        loop = asyncio.get_running_loop()

        def cancel() -> bool:
            if task.done():
                return False
            loop.call_soon_threadsafe(task.cancel)
            return True

        self.attach_killer(task_id, cancel)
        return task_id

    def _publish(self, task_id: str) -> None:
        """发布任务事实，展示层不自行猜测后台是否完成。"""
        if self._loop is not None and self._loop.is_running() and threading.current_thread() is not threading.main_thread():
            self._loop.call_soon_threadsafe(self._publish, task_id)
            return
        rec = self._records.get(task_id)
        if rec is None or not rec.info.turn_id or self._closing:
            return
        payload = {"turn_id": rec.info.turn_id, "task_id": task_id, "scope": rec.info.scope,
                   "kind": rec.info.kind, "description": rec.info.description,
                   "status": ("done" if rec.success else "error") if rec.done else "running",
                   "started_at": rec.info.started_at, "finished_at": rec.finished_at,
                   "result": rec.summary, "notification": rec.info.notification.value}
        self._track(event_bus.emit(EVENT_BACKGROUND_TASK_UPDATED, payload), name="background.publish")

    async def close(self) -> None:
        """停止接收任务并回收本注册表托管的协程和提醒计时器。"""
        with self._admission_lock:
            self._closing = True
        self._on_unclaimed = None
        self._on_alert = None
        for rec in self._snapshot_records():
            if rec.stall_timer is not None:
                rec.stall_timer.cancel()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def attach_killer(self, task_id: str, killer: Callable[[], bool]) -> None:
        """为任务关联终止句柄（terminate 用；shell 进程组击杀 / 委托取消）。"""
        rec = self._records.get(task_id)
        if rec is not None and callable(killer):
            rec.killer = killer

    def attach_progress(self, task_id: str, reader: Callable[[], Dict[str, Any]]) -> None:
        """关联内存进度快照；完成后冻结数据并释放执行体引用。"""
        rec = self._records.get(task_id)
        if rec is not None and not rec.done:
            rec.progress_reader = reader

    def progress(self, task_id: str) -> Dict[str, Any]:
        """读取生产者提供的阶段事实，不消费输出或完成通知。"""
        rec = self._records.get(task_id)
        if rec is None:
            return {}
        return dict(rec.progress_reader() if rec.progress_reader else rec.progress)

    def record_evidence(self, task_id: str, kind: str, **facts: Any) -> None:
        """保留生产者的操作证据，任务完成后拒绝迟到事件。"""
        rec = self._records.get(task_id)
        if rec is not None and not rec.done:
            rec.evidence.append(kind, **facts)

    def watch_stall(self, task_id: str, seconds: float) -> None:
        """无进展达到阈值时提醒一次；有新进展后重新计时，不终止任务。"""
        rec = self._records.get(task_id)
        if rec is None or rec.done:
            return
        if rec.stall_timer is not None:
            rec.stall_timer.cancel()
            rec.stall_timer = None
        if seconds <= 0:
            return
        loop = asyncio.get_running_loop()

        def check() -> None:
            rec.stall_timer = None
            if rec.done:
                return
            progress = self.progress(task_id)
            updated_at = float(progress.get("updated_at", rec.info.started_at))
            idle = max(0.0, time.time() - updated_at)
            if idle >= seconds and rec.alerted_progress_at != updated_at:
                rec.alerted_progress_at = updated_at
                self.alert_timeout(task_id, f"连续 {int(idle)} 秒无新进展；阶段={progress.get('phase', 'running')}，"
                                   f"操作={progress.get('operation', '')}。耗时不代表失败，请查询证据后判断是否继续。")
            rec.stall_timer = loop.call_later(max(1.0, seconds - idle) if idle < seconds else seconds, check)

        rec.stall_timer = loop.call_later(seconds, check)

    # ------------------------------------------------------------------
    # 增量输出读取（单游标消费型：每次只返回上次读取之后的新增输出）
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
            if i + step > n:
                return data[:i], True
            i += step
            count += 1
        return data, False

    def read_task_output(self, scope: str, task_id: str, max_chars: int = 8000,
                         *, after_sequence: int = 0) -> Dict[str, Any]:
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
        facts = {"notification": rec.info.notification.value,
                 "evidence": rec.evidence.read(after_sequence)}
        if not rec.output_file:
            return {"ok": True, "task_id": task_id, "done": rec.done, **facts,
                    "elapsed_seconds": max(0, int((rec.finished_at or time.time()) - rec.info.started_at)),
                    "progress": self.progress(task_id), "summary": rec.summary,
                    "success": rec.success if rec.done else None,
                    "hint": "evidence 为有界操作证据；传 after_sequence=next_sequence 获取后续记录。耗时或暂无进展不代表失败。通知遵循 notification 策略。"}
        offset = self._output_cursors.get(task_id, 0)
        try:
            with open(rec.output_file, "rb") as f:
                f.seek(offset)
                chunk = f.read(max_chars * 4 + 16)
        except OSError as exc:
            return {"ok": False, "error": f"读取任务输出失败: {exc}"}
        # 回退尾部可能不完整的多字节序列（文件仍在写入时最后一字符可能被截半）
        consumed, truncated = self._cut_utf8(chunk, max_chars)
        delta = consumed.decode("utf-8", errors="replace").replace("\r\n", "\n")
        self._output_cursors[task_id] = offset + len(consumed)
        return {
            "ok": True,
            **facts,
            "task_id": task_id,
            "delta": delta,
            "consumed_bytes": offset + len(consumed),
            "truncated": truncated,
            "done": rec.done,
            "hint": "delta 为本次新增输出（消费型读取，重复调用不会重发旧内容）；truncated=true 表示还有未读。" if delta else "暂无新输出，可稍后再查或等待完成通知。",
        }

    def complete(
            self, task_id: str, success: bool, summary: str,
            *, claimed: Optional[bool] = None, has_output: bool = True,
    ) -> bool:
        """标记任务完成并唤醒等待者（线程安全）。

        主循环线程内直接完成；工作线程（如后台 shell 等待线程）经
        call_soon_threadsafe 回到主循环完成，此时返回值无意义（恒 True）。
        claimed 非 None 时覆盖"是否存在等待者"的判定：调用方声明结果已被
        自身消费（如前台委托的工具结果即通知本体）时传 True，跳过轮外
        完成回调，避免同一完成事件双投递。

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
            self._loop.call_soon_threadsafe(self._finish, task_id, success, summary, claimed, has_output)
            return True
        return self._finish(task_id, success, summary, claimed, has_output)

    def _finish(
            self, task_id: str, success: bool, summary: str,
            claimed_override: Optional[bool] = None,
            has_output: bool = True,
    ) -> bool:
        """完成处理的实际实现（须运行在主循环线程）。"""
        rec = self._records.get(task_id)
        if rec is None or rec.done:
            return True
        rec.done = True
        rec.success = success
        rec.summary = summary
        rec.finished_at = time.time()
        rec.progress = self.progress(task_id)
        rec.progress_reader = None
        rec.killer = None
        if rec.stall_timer is not None:
            rec.stall_timer.cancel()
            rec.stall_timer = None
        if not rec.info.notification.accepts(success, has_output):
            claimed_override = True

        claimed = (
            any(target == task_id or (not target and rec.info.joinable)
                for target in self._wait_targets.get(rec.info.scope, []))
            if claimed_override is None else claimed_override
        )
        if not claimed or claimed_override is True:
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
        self._publish(task_id)
        # 轮外完成（无等待者）：触发回调（由运行时接入，写对话历史并排入
        # 回复队列触发新 REPLY；非 conversation scope 由回调侧全局桶兜底）。
        # 通知失败时恢复未送达标记——先标记后投递会让回调异常静默丢通知
        if not claimed:
            self._notify(rec)
        return claimed

    def _notify(self, rec: _TaskRecord) -> None:
        """投递未被等待者领取的结果；失败时保留查询和再次领取的机会。"""
        if self._on_unclaimed is None or self._closing:
            return
        rec.delivered = True
        try:
            result = self._on_unclaimed(
                rec.info.scope, rec.info.description,
                tag_label("task_id", rec.info.task_id) + " " + rec.summary[:1500], rec.success,
            )
        except Exception as exc:
            rec.delivered = False
            log(f"后台任务完成通知失败: {rec.info.task_id}: {exc}", "ERROR", tag="后台")
            return
        if inspect.isawaitable(result):
            async def notify() -> None:
                try:
                    await result
                except Exception as exc:
                    rec.delivered = False
                    log(f"后台任务完成通知失败: {rec.info.task_id}: {exc}", "ERROR", tag="后台")
            self._track(notify(), name="background.notify")

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
        try:
            result = self._on_alert(rec.info.scope, rec.info.description, detail, task_id)
        except Exception as exc:
            log(f"后台进度提醒失败: {task_id}: {exc}", "ERROR", tag="后台")
            return
        if inspect.iscoroutine(result):
            async def notify() -> None:
                try:
                    await result
                except Exception as exc:
                    log(f"后台进度提醒失败: {task_id}: {exc}", "ERROR", tag="后台")
            self._track(notify(), name="background.alert")

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
            "notification": rec.info.notification.value,
            "hint": "终止信号已受理；最终状态由任务确认，可继续查询。完成通知遵循 notification 策略。" if terminated else "终止信号未受理，请查询当前状态。",
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def running(self, scope: str, *, joinable_only: bool = False) -> List[BackgroundTaskInfo]:
        """当前会话运行中的任务；会合查询排除不应阻塞主回复的内部维护。"""
        return [
            rec.info for rec in self._snapshot_records()
            if rec.info.scope == scope and not rec.done
            and (not joinable_only or rec.info.joinable)
        ]

    async def cancel_scope(self, scope: str) -> list[Dict[str, Any]]:
        """请求停止会话后台工作；保留结果供查询，取消回执不再自动唤醒会话。"""
        tasks = self.running(scope)
        for task in tasks:
            record = self._records[task.task_id]
            record.info.notification = CompletionNotification.SILENT
            record.info.expected_seconds = 0
            self.watch_stall(task.task_id, 0)
        return await asyncio.gather(*(
            asyncio.to_thread(self.terminate, scope, task.task_id) for task in tasks
        ))

    def completed(self, scope: str) -> List[TaskCompletion]:
        """该 scope 下已完成的任务结果（含已送达，供主动查询回看）。"""
        return [
            rec.to_completion() for rec in self._snapshot_records()
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
                    "progress": self.progress(t.task_id),
                    "notification": t.notification.value,
                    "joinable": t.joinable,
                }
                for t in self.running(scope)
            ],
            "completed": [
                {
                    "task_id": c.task_id,
                    "kind": c.kind,
                    "description": c.description,
                    "success": c.success,
                    "summary": c.summary[:500],
                    "finished_at": c.finished_at,
                    "notification": c.notification.value,
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
            *, task_id: str = "",
    ) -> WaitResult:
        """挂起等待该 scope 任一后台任务或指定任务完成。

        1 秒粒度轮询，每轮检查 should_abort（中断信号/新消息到达），
        超时或被打断时安全返回，不消费任何完成事件。

        Args:
            scope: 对话 scope
            timeout: 等待上限（秒）
            should_abort: 可选的中止判定（返回 True 立即以 interrupted 结束）
            task_id: 指定时仅等待该任务，已完成的任务直接返回保留结果。
        """
        if task_id:
            rec = self._records.get(task_id)
            if rec is None or rec.info.scope != scope:
                raise ValueError("任务不存在或不属于当前会话")
            if rec.done:
                rec.delivered = True
                return WaitResult(reason="completed", completions=[rec.to_completion()])
        event = self._events.setdefault(scope, asyncio.Event())
        self._wait_targets.setdefault(scope, []).append(task_id)
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            while True:
                selected = self._records.get(task_id) if task_id else None
                fresh = ([selected] if selected and selected.done else []) if task_id else [
                    rec for rec in self._collect_undelivered(scope) if rec.info.joinable
                ]
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
            self._wait_targets[scope].remove(task_id)
            if not self._wait_targets[scope]:
                self._wait_targets.pop(scope)
            for rec in self._collect_undelivered(scope):
                if not any(target == rec.info.task_id or (not target and rec.info.joinable)
                           for target in self._wait_targets.get(scope, [])):
                    self._notify(rec)
            # scope 无等待者且无运行中任务时回收事件与计数，防按 scope 无限累积
            if scope not in self._wait_targets and not self.running(scope):
                if not self._collect_undelivered(scope):
                    self._events.pop(scope, None)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _snapshot_records(self) -> tuple[_TaskRecord, ...]:
        """读取结构快照，避免工作线程登记与状态遍历互相踩踏。"""
        with self._admission_lock:
            return tuple(self._records.values())

    def _collect_undelivered(self, scope: str) -> List[_TaskRecord]:
        return [
            rec for rec in self._snapshot_records()
            if rec.info.scope == scope and rec.done and not rec.delivered
        ]

    def _purge_completed(self, scope: str) -> None:
        """已完成记录超出上限时丢弃最旧的（只影响历史回看，不影响运行态）。"""
        done = [
            rec for rec in self._snapshot_records()
            if rec.info.scope == scope and rec.done
        ]
        if len(done) <= _MAX_COMPLETED_PER_SCOPE:
            return
        done.sort(key=lambda rec: rec.finished_at)
        for rec in done[: len(done) - _MAX_COMPLETED_PER_SCOPE]:
            self._records.pop(rec.info.task_id, None)
            self._output_cursors.pop(rec.info.task_id, None)
