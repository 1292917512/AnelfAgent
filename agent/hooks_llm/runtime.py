"""钩子运行时：事件装配 + 生命周期宿主 + LateBinding 端口。

HookRuntime 把注册表中的钩子装配到事件总线（按钩子 event 映射到具体
EVENT_* 常量订阅），并把命中事件转发给 HookExecutor 并行拉起；自身经
LateBinding 端口由 wiring 统一施绑（Mind 创建后实例化，仅分发引用）。

事件映射（钩子事件 → event_bus 事件）：
- after_reply         → EVENT_AFTER_REPLY（payload 含 messages 快照）
- context_pressure    → EVENT_CONTEXT_USAGE（每轮上下文用量，带 tokens/percent）
- delegation_resolved → EVENT_DELEGATION_RESOLVED

订阅用低 priority（让主流程 handler 先执行）+ owner=hooks_llm（便于
批量清理）；订阅幂等，重复装配不产生重复 handler。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from agent.hooks_llm.executor import HookExecutor
from agent.hooks_llm.spec import (
    HOOK_EVENT_AFTER_REPLY,
    HOOK_EVENT_CONTEXT_PRESSURE,
    HOOK_EVENT_DELEGATION_RESOLVED,
    HOOK_EVENT_LLM_END,
    HookRegistry,
)
from core.event_bus import (
    EVENT_AFTER_REPLY,
    EVENT_DELEGATION_RESOLVED,
    EVENT_THINKING_LLM_END,
    event_bus,
)
from core.log import log
from core.stream_events import EVENT_CONTEXT_USAGE

if TYPE_CHECKING:
    from agent.mind.mind import Mind

# 钩子事件 → event_bus 事件常量映射
_EVENT_SOURCE: Dict[str, str] = {
    HOOK_EVENT_AFTER_REPLY: EVENT_AFTER_REPLY,
    HOOK_EVENT_CONTEXT_PRESSURE: EVENT_CONTEXT_USAGE,
    HOOK_EVENT_DELEGATION_RESOLVED: EVENT_DELEGATION_RESOLVED,
    HOOK_EVENT_LLM_END: EVENT_THINKING_LLM_END,
}

_OWNER = "hooks_llm"
# 订阅优先级：低于主流程 handler，让核心消费（如 SkillReviewer 若并存）先执行
_SUBSCRIBE_PRIORITY = -10


class HookRuntime:
    """钩子运行时：事件装配 + 执行器宿主 + 关停清理。"""

    def __init__(self, mind: "Mind") -> None:
        self._mind = mind
        from core.config import get_config_int
        self._executor = HookExecutor(
            mind, pool_size=get_config_int("hooks_llm_max_concurrent", 2),
        )
        self._started = False

    @property
    def executor(self) -> HookExecutor:
        return self._executor

    def start(self) -> None:
        """装配事件订阅（幂等）。"""
        if self._started:
            return
        if not self._enabled():
            log("LLM 钩子面已禁用（hooks_llm_enabled=false）", "DEBUG", tag="钩子")
            return
        event_bus.on(EVENT_AFTER_REPLY, self._on_after_reply,
                     priority=_SUBSCRIBE_PRIORITY, owner=_OWNER)
        event_bus.on(EVENT_CONTEXT_USAGE, self._on_context_pressure,
                     priority=_SUBSCRIBE_PRIORITY, owner=_OWNER)
        event_bus.on(EVENT_DELEGATION_RESOLVED, self._on_delegation_resolved,
                     priority=_SUBSCRIBE_PRIORITY, owner=_OWNER)
        # llm_end 高频事件：仅当有钩子注册时才有实际消费（_forward 空注册短路），
        # 订阅本身零开销
        event_bus.on(EVENT_THINKING_LLM_END, self._on_llm_end,
                     priority=_SUBSCRIBE_PRIORITY, owner=_OWNER)
        self._started = True
        log(f"LLM 钩子面已启动（{len(HookRegistry.list_all())} 个钩子）", tag="钩子")

    def stop(self) -> None:
        """解除事件订阅并停掉未开始的防抖任务（进水口先停，幂等）。"""
        if not self._started:
            return
        event_bus.off_by_owner(_OWNER)
        self._executor.cancel_pending()
        self._started = False

    async def drain(self, timeout: float = 15.0) -> None:
        """关停 drain：停订阅后等待运行中的钩子自然收尾（思考后收）。

        先 stop() 切断新触发（进水口），再等已开始执行的钩子完成——
        进行中的评审/分析能写完，不被硬取消打断。供 Lifecycle cleanup 调用。
        """
        self.stop()
        await self._executor.drain(timeout=timeout)

    @staticmethod
    def _enabled() -> bool:
        """钩子面总开关。"""
        from core.config import get_config_bool
        return get_config_bool("hooks_llm_enabled", True)

    # ------------------------------------------------------------------
    # 事件转发（→ executor.dispatch）
    # ------------------------------------------------------------------

    async def _on_after_reply(self, payload: Dict[str, Any]) -> None:
        await self._forward(HOOK_EVENT_AFTER_REPLY, payload)

    async def _on_context_pressure(self, payload: Dict[str, Any]) -> None:
        await self._forward(HOOK_EVENT_CONTEXT_PRESSURE, payload)

    async def _on_delegation_resolved(self, payload: Dict[str, Any]) -> None:
        await self._forward(HOOK_EVENT_DELEGATION_RESOLVED, payload)

    async def _on_llm_end(self, payload: Dict[str, Any]) -> None:
        await self._forward(HOOK_EVENT_LLM_END, payload)

    async def _forward(self, hook_event: str, payload: Dict[str, Any]) -> None:
        """转发事件到命中钩子（后台调度，立即返回）；无注册时零开销短路。

        dispatch 只做条件门控与后台 task 调度，不 await 钩子执行——
        emit 方（complete_reply / llm_invoker）不会因钩子 LLM 而阻塞。
        """
        specs = HookRegistry.for_event(hook_event)
        if not specs:
            return
        try:
            self._executor.dispatch(hook_event, specs, payload)
        except Exception as exc:
            log(f"钩子事件 {hook_event} 分发异常: {exc}", "WARNING", tag="钩子")


from core.latebind import LateBinding  # noqa: E402

# 运行时晚绑定端口（wiring 在 Mind 创建后施绑；未施绑 get() 抛 WireError）
hooks_llm_runtime_port: LateBinding[Optional[HookRuntime]] = LateBinding(
    "hooks_llm.runtime"
)


def get_hook_runtime() -> Optional[HookRuntime]:
    """取钩子运行时（未施绑返回 None，保持可选消费语义）。"""
    if not hooks_llm_runtime_port.bound:
        return None
    return hooks_llm_runtime_port.get()
