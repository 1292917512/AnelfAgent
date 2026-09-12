"""钩子并行执行器：事件命中 → 多钩子并发拉起 + 治理。

同一钩子位置（event）可挂多个钩子，命中后 asyncio.gather 并发执行，
每个钩子独立的治理桶：per-hook 并发信号量、per-hook-per-scope
cooldown/debounce、独立日志归因 / 用量归属 / 进度流 / 后台任务条目。
单钩子失败或超时不影响同事件其他钩子（各自 try/except 隔离）。

治理件全部复用既有原语，不重造：
- 并发：全局池 semaphore（总量护栏）+ per-hook semaphore；
- 频控：cooldown（最小间隔）+ debounce（合并窗口取最后快照）；
- 防递归：执行体标 origin=llm_hook 深度标记，其派生事件不再触发同类钩子；
- 观测：bind_log_actor / bind_usage_scope / journal 进度流；
- 路由：BackgroundTaskRegistry 登记，完成走既有 unclaimed → wake_budget 通道。
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.hooks_llm.snapshot import prepare_hook_messages
from agent.hooks_llm.spec import HookContext, HookContextMode, LLMHookSpec
from core.log import bind_log_actor, log, reset_log_actor

if TYPE_CHECKING:
    from agent.mind.mind import Mind

# 钩子 LLM 执行的来源标记（防递归识别 + 日志/事件归因）
HOOK_ORIGIN = "llm_hook"


class _RecursionGuard:
    """防递归：钩子 LLM 执行期间，其派生事件不再触发同类钩子。

    经 ContextVar 标记当前执行树已在钩子内（create_task 复制进整个
    执行树），事件分发前置检查命中即跳过——钩子产出（如评审写入）引发
    的 after_reply / context_pressure 不会再拉起钩子，阻断自我激励循环。
    """

    from contextvars import ContextVar
    _in_hook: "ContextVar[bool]" = ContextVar("hooks_llm_in_hook", default=False)

    @classmethod
    def active(cls) -> bool:
        """当前执行树是否已在钩子内（命中则跳过再触发）。"""
        return cls._in_hook.get()

    @classmethod
    def enter(cls):
        """进入钩子执行（返回复位令牌）。"""
        return cls._in_hook.set(True)

    @classmethod
    def exit(cls, token) -> None:
        """退出钩子执行。"""
        cls._in_hook.reset(token)


class HookExecutor:
    """钩子并行执行器（持 mind 与治理状态，由 runtime 装配）。"""

    def __init__(self, mind: "Mind", *, pool_size: int = 2) -> None:
        self._mind = mind
        self._pool = asyncio.Semaphore(max(1, pool_size))
        # per-hook 并发信号量（按 spec.name 惰性创建）
        self._hook_sems: Dict[str, asyncio.Semaphore] = {}
        # per-hook-per-scope 上次实际执行时刻（cooldown 计时锚点）
        self._last_fired: Dict[str, float] = {}
        # per-key 防抖：最新快照槽 + 单个待发执行（call_later 句柄）
        self._debounce_payload: Dict[str, tuple[LLMHookSpec, Dict[str, Any], str]] = {}
        self._debounce_handle: Dict[str, asyncio.TimerHandle] = {}
        # 在途钩子执行任务（关停回收 + 防泄漏）
        self._running: set[asyncio.Task] = set()
        # 关停中标志：drain 期间拒绝新 dispatch（防 drain 返回后仍有新 task 残留）
        self._draining: bool = False

    # ------------------------------------------------------------------
    # 事件入口（同事件多钩子并行）
    # ------------------------------------------------------------------

    def dispatch(self, event: str, specs: List[LLMHookSpec],
                 payload: Dict[str, Any]) -> None:
        """分发事件到全部命中钩子（后台调度，立即返回）。

        不 await 任何钩子执行——钩子经 event_bus.emit 的 handler 链触发，
        若在调用栈里 await 会让 emit 方（如 complete_reply）同步阻塞到钩子
        LLM 跑完，违背"异步扩员"语义。每个钩子的真实执行在独立 task 中，
        登记到 _running 供关停回收。

        防递归前置：当前已在钩子执行树内（钩子的 LLM 产出派生的事件）
        时整批跳过，阻断自我激励循环。
        """
        if self._draining:
            return  # 关停中：不再受理新工作
        if _RecursionGuard.active():
            log(f"钩子事件 {event} 在钩子内派生，跳过再触发（防递归）", "DEBUG", tag="钩子")
            return
        for spec in specs:
            if self._passes_when(spec, payload):
                self._schedule(spec, payload)

    @staticmethod
    def _passes_when(spec: LLMHookSpec, payload: Dict[str, Any]) -> bool:
        """条件门控：when 缺失恒过；异常视为不触发（fail-closed，防抖触发）。"""
        if spec.when is None:
            return True
        try:
            return bool(spec.when(payload))
        except Exception as exc:
            log(f"钩子 {spec.name} 条件门控异常: {exc}", "DEBUG", tag="钩子")
            return False

    # ------------------------------------------------------------------
    # 单钩子调度（冷却 / 防抖 / 后台执行）
    # ------------------------------------------------------------------

    def _sem_for(self, spec: LLMHookSpec) -> asyncio.Semaphore:
        """取该钩子的并发信号量（惰性创建）。"""
        sem = self._hook_sems.get(spec.name)
        if sem is None:
            sem = asyncio.Semaphore(max(1, spec.max_concurrent))
            self._hook_sems[spec.name] = sem
        return sem

    def _throttle_key(self, spec: LLMHookSpec, scope: str) -> str:
        """频控键（per-hook per-scope）。"""
        return f"{spec.name}|{scope or '_global'}"

    def _on_cooldown(self, spec: LLMHookSpec, scope: str) -> bool:
        """是否处于冷却期（以实际执行时刻为锚）。"""
        if spec.cooldown_seconds <= 0:
            return False
        last = self._last_fired.get(self._throttle_key(spec, scope), 0.0)
        return (time.monotonic() - last) < spec.cooldown_seconds

    def _schedule(self, spec: LLMHookSpec, payload: Dict[str, Any]) -> None:
        """调度单个钩子：冷却检查 → 防抖合并或立即后台执行。"""
        scope = self._resolve_scope(spec, payload)
        if self._on_cooldown(spec, scope):
            log(f"钩子 {spec.name} 冷却期内跳过", "DEBUG", tag="钩子")
            return
        if spec.debounce_seconds > 0:
            self._debounce(spec, payload, scope)
        else:
            self._spawn(spec, payload, scope)

    def _debounce(self, spec: LLMHookSpec, payload: Dict[str, Any], scope: str) -> None:
        """防抖合并：同 key 只保留最新快照 + 一个待发句柄。

        新触发更新快照槽并重排 call_later——对未触发的 TimerHandle 取消是
        确定性的（不像 task.cancel 协作式取消可能让运行中的实例跑完），
        因此"窗口内合并为一次、取最后快照"是结构保证而非时序运气。
        """
        key = self._throttle_key(spec, scope)
        self._debounce_payload[key] = (spec, payload, scope)
        handle = self._debounce_handle.get(key)
        if handle is not None:
            handle.cancel()
        loop = asyncio.get_running_loop()
        self._debounce_handle[key] = loop.call_later(
            spec.debounce_seconds, self._fire_debounced, key,
        )

    def _fire_debounced(self, key: str) -> None:
        """防抖窗口结束：取最新快照后台执行（槽/句柄清理）。"""
        self._debounce_handle.pop(key, None)
        entry = self._debounce_payload.pop(key, None)
        if entry is None:
            return
        spec, payload, scope = entry
        self._spawn(spec, payload, scope)

    def _spawn(self, spec: LLMHookSpec, payload: Dict[str, Any], scope: str) -> None:
        """后台拉起钩子执行（独立 task，登记回收）。

        冷却锚点在此（调度受理时刻）同步记录：连续同步触发时第一个后台
        task 尚未及执行，若在执行时才记录会漏判冷却导致重复受理。池排队
        不造成语义偏差——同一钩子的实例本就由 per-hook 信号量串行执行，
        冷却针对的是"该钩子已受理/在忙"的窗口，排队等待属于该窗口。
        """
        self._last_fired[self._throttle_key(spec, scope)] = time.monotonic()
        task = asyncio.create_task(
            self._fire(spec, payload, scope),
            name=f"hooks_llm.{spec.name}",
        )
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    @staticmethod
    def _resolve_scope(spec: LLMHookSpec, payload: Dict[str, Any]) -> str:
        """解析触发会话 scope：payload 优先；llm_end 的 payload 无 scope，
        从思维上下文 ContextVar 推（事件在 think_loop 执行树内发射）。"""
        scope = str(payload.get("scope") or "")
        if scope:
            return scope
        try:
            from agent.mind.tool_activation import ToolActivationManager
            return ToolActivationManager.current_scope() or ""
        except Exception:
            return ""

    async def _fire(self, spec: LLMHookSpec,
                    payload: Dict[str, Any], scope: str) -> None:
        """在独立 task 中执行钩子（并发池 + per-hook 并发 + 观测 + 路由）。

        由 _spawn 调度（冷却锚点已记）；本函数只负责真正的执行与隔离。
        """
        ctx = HookContext(
            name=spec.name, event=spec.event, scope=scope,
            payload=payload, trigger=spec.event,
            messages=self._build_messages(spec, payload),
        )
        hook_sem = self._sem_for(spec)
        async with self._pool:
            async with hook_sem:
                await self._execute(spec, ctx)

    def _build_messages(self, spec: LLMHookSpec,
                        payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """按档位构建上下文快照（transcript 取 payload.messages）。

        transcript 档位受 hooks_llm_transcript_enabled 门控：关闭时降级为
        无快照（钩子仍可执行，只是拿不到完整上下文），防大上下文成本。
        payload.messages 的来源：after_reply 由 completion 容器带出、llm_end
        由 llm_invoker 附带本次调用的发送消息链——两者均为「刚用完的上下文」，
        钩子无需现场抓取。
        """
        if spec.context is HookContextMode.TRANSCRIPT:
            if not self._transcript_enabled():
                return []
            return prepare_hook_messages(payload.get("messages"))
        if spec.context is HookContextMode.LEAN:
            # lean 前缀（人设+工具+永久记忆）由执行体经 mind.get_recollection
            # 现取——快照层不预构建，避免为不消费它的钩子白付组装成本
            return []
        return []

    @staticmethod
    def _transcript_enabled() -> bool:
        """transcript 快照总开关。"""
        try:
            from core.config import get_config_bool
            return get_config_bool("hooks_llm_transcript_enabled", True)
        except Exception:
            return True

    async def _execute(self, spec: LLMHookSpec, ctx: HookContext) -> None:
        """钩子 LLM 执行体（独立日志归因 / 用量归属 / 进度流 / 后台任务条目）。"""
        token = _RecursionGuard.enter()
        actor = bind_log_actor(f"钩子@{spec.name}")
        usage_token = None
        if ctx.scope:
            from agent.mind.scope_usage import bind_usage_scope
            usage_token = bind_usage_scope(ctx.scope)
        try:
            output = await spec.handler(ctx)
            log(f"钩子 {spec.name} 完成 (event={spec.event})", "DEBUG", tag="钩子")
            self._route_result(spec, ctx, output)
        except asyncio.CancelledError:
            log(f"钩子 {spec.name} 被取消", "DEBUG", tag="钩子")
            raise
        except Exception as exc:
            log(f"钩子 {spec.name} 执行失败: {type(exc).__name__}: {exc}",
                "WARNING", tag="钩子")
        finally:
            if usage_token is not None:
                from agent.mind.scope_usage import reset_usage_scope
                reset_usage_scope(usage_token)
            reset_log_actor(actor)
            _RecursionGuard.exit(token)

    def _route_result(self, spec: LLMHookSpec, ctx: HookContext,
                      output: Optional[str]) -> None:
        """产出路由：登记后台任务完成（走既有 unclaimed → wake_budget 通道）。

        仅当钩子产出非空且宿主有后台任务注册表时登记；登记失败仅记日志，
        绝不影响主对话。
        """
        if not output:
            return
        registry = getattr(self._mind, "background_tasks", None)
        if registry is None:
            return
        try:
            task_id = registry.register(
                ctx.scope or "_global", "llm_hook",
                f"{spec.name}: {spec.event}",
            )
            registry.complete(task_id, True, str(output)[:1500])
        except Exception as exc:
            log(f"钩子 {spec.name} 结果登记失败: {exc}", "DEBUG", tag="钩子")

    # ------------------------------------------------------------------
    # 关停
    # ------------------------------------------------------------------

    def cancel_pending(self) -> None:
        """取消防抖待发（未开始执行的）——立即生效，不等在途任务。

        仅停掉"还没开始"的工作；已开始执行的钩子由 drain() 等待收尾。
        """
        for handle in self._debounce_handle.values():
            handle.cancel()
        self._debounce_handle.clear()
        self._debounce_payload.clear()

    async def drain(self, timeout: float = 15.0) -> None:
        """关停 drain：取消防抖待发后等待在途钩子自然收尾，超时才取消。

        已开始执行的钩子（如写了一半技能库的评审）给它们完成的机会——
        硬取消会在 reflect 内部某个 await 点抛 CancelledError，留下逻辑上
        不完整的中间态。与 Lifecycle 的 drain 语义一致（进水口先停、思考后收）。
        """
        self._draining = True
        self.cancel_pending()
        running = [t for t in self._running if not t.done()]
        if not running:
            return
        done, pending = await asyncio.wait(running, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            # 等取消生效（协作式，给 finally 清理一次机会）
            await asyncio.gather(*pending, return_exceptions=True)
            log(f"关停取消 {len(pending)} 个超时钩子任务", "WARNING", tag="钩子")
