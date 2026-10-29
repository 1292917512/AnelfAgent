"""内部反思执行：工具选择、隔离会话与多轮推理。

模型可见：沿用内部会话消息、工具目录和输出权限。
Token 影响：不改变提示词或新增模型调用。
缓存影响：沿用原工具顺序、会话隔离与上下文分层。
"""

from __future__ import annotations

import time
import uuid
from contextlib import nullcontext
from typing import TYPE_CHECKING

from agent.mind.execution.loop import think_loop
from agent.mind.execution.state import ThinkMode
from agent.mind.execution.tool_execution import _await_with_abort
from agent.mind.think_session import think_session
from agent.mind.tools.core import build_reflect_blocklist, select_reflect_tools
from core.activity import activity_scope, activity_step
from core.log import log
from core.tool_context import tool_request

if TYPE_CHECKING:
    from agent.mind.mind import Mind


async def reflect_entry(
    mind: Mind, messages: list[dict], *, adapter_key: str = "", max_iterations: int = 0,
    options: dict | None = None, tool_tags: list[str] | None = None,
    allow_output_tools: bool = True, extra_blocked_tools: set[str] | None = None,
    completion: dict | None = None, require_output: bool = False,
    inherited_tools: list[dict] | None = None,
    iteration_config_keys: tuple[str, ...] = (),
) -> str:
    """按内部会话的工具权限执行反思，返回收束后的文本。

    tool_tags 按标签或分组选择工具；inherited_tools 保留触发轮工具顺序。
    max_iterations 为 0 时沿用主思维配置；最终上限为 0 时不限制轮次。
    completion 接收执行终态；require_output 在剩余预算内要求提交总结。
    每次执行使用独立 scope，退出时统一释放绑定及工具激活状态。
    """
    safety_limit = max_iterations or mind.config.max_tool_iterations
    if not max_iterations:
        iteration_config_keys = ("max_tool_iterations",)
    blocked_tools = build_reflect_blocklist(allow_output_tools)
    if extra_blocked_tools:
        blocked_tools = blocked_tools | set(extra_blocked_tools)
    reflect_scope = f"reflect:{uuid.uuid4().hex[:8]}"
    collected_text: list[str] = []
    execution_steps: list[str] = []
    output_policy = "放开外发" if allow_output_tools else "禁用外发"
    interrupts = getattr(mind, "interrupts", None)
    turn_id = uuid.uuid4().hex[:8]
    with think_session(mind.tool_assembly, reflect_scope, with_token=False), tool_request(reflect_scope, inherit=True), \
            interrupts.execution(turn_id, reflect_scope) if interrupts is not None else nullcontext():
        async with activity_scope(turn_id, reflect_scope, can_stop=True) as activity:
            if completion is not None:
                completion["turn_id"] = turn_id
            async with activity_step("tool_prepare") as step:
                prepared, aborted = await _await_with_abort(select_reflect_tools(
                    mind.tool_assembly, adapter_key=adapter_key, scope=reflect_scope,
                    tool_tags=tool_tags, inherited_tools=inherited_tools,
                ), interrupts.event(reflect_scope) if interrupts is not None else None)
                if aborted:
                    step.status = "interrupted"
            if aborted:
                activity.status = "cancelled"
                if completion is not None:
                    completion.update(reason="interrupted", turn_id=turn_id)
                return ""
            active_tools, extra_selectors = prepared
            limit_hint = f"上限 {safety_limit} 轮" if safety_limit > 0 else "不设轮次上限"
            log(f"反思循环开始: {len(active_tools)} 个工具可用, 策略={output_policy}, {limit_hint}", tag="思维")
            await think_loop(
                mind,
                mode=ThinkMode.REFLECT,
                tool_chain=[],
                execution_steps=execution_steps,
                start_time=time.time(),
                safety_limit=safety_limit,
                collected_text=collected_text,
                active_tools=active_tools,
                anything=None,
                base_messages=messages,
                options=options,
                blocked_tools=blocked_tools,
                completion=completion,
                reflect_tool_selectors=extra_selectors,
                require_output=require_output,
                activity=activity,
                iteration_config_keys=iteration_config_keys,
            )
    total = "\n".join(collected_text)
    log(f"反思循环结束: 产出 {len(total)} 字", tag="思维")
    return total
