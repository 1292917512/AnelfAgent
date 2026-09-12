"""LLM 钩子面：在 LLM 思考边界派生带上下文的异步 LLM 工作的统一注册原语。

与心跳/任务系统平行、与 shell 钩子（agent/hooks，同步阻塞守门）分层：
本面是异步并行扩员——同一钩子位置（事件）可挂多个钩子，命中后并发
拉起，各自独立的治理桶（并发/频控/防递归/观测/路由）。

消费者（互不绑定，经各自入口接入）：
- 技能系统：后台评审经钩子面继承完整 transcript（agent/skills）；
- 任务系统：任务可配置 trigger_event 事件触发（agent/task）；
- 实体：经 entities._sdk.register_entity_llm_hook 桥接注册。

使用::

    from agent.hooks_llm import llm_hook, HookContext

    @llm_hook("my_hook", event="after_reply", context="transcript",
              tool_tags=["memory"], max_iterations=6)
    async def my_hook(ctx: HookContext) -> Optional[str]:
        ...
"""
from __future__ import annotations

from agent.hooks_llm import configs as _configs  # noqa: F401  # 注册配置项
from agent.hooks_llm.executor import HOOK_ORIGIN, HookExecutor
from agent.hooks_llm.runtime import (
    HookRuntime,
    get_hook_runtime,
    hooks_llm_runtime_port,
)
from agent.hooks_llm.snapshot import (
    cap_snapshot_chars,
    freeze_messages,
    prepare_hook_messages,
)
from agent.hooks_llm.spec import (
    HOOK_EVENTS,
    HookContext,
    HookContextMode,
    HookRegistry,
    LLMHookSpec,
    llm_hook,
)

__all__ = [
    "llm_hook",
    "HookContext",
    "HookContextMode",
    "LLMHookSpec",
    "HookRegistry",
    "HOOK_EVENTS",
    "HOOK_ORIGIN",
    "HookExecutor",
    "HookRuntime",
    "get_hook_runtime",
    "hooks_llm_runtime_port",
    "freeze_messages",
    "cap_snapshot_chars",
    "prepare_hook_messages",
]
