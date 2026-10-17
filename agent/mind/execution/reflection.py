"""内部反思入口：独立会话、工具权限与执行收尾。"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from agent.mind import tool_activation as _tool_activation  # noqa: F401  # 注册 activate_tool_group 延迟工具
from agent.mind.execution.state import ThinkMode
from agent.mind.mind_tools import build_reflect_blocklist, select_reflect_tools
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


async def reflect(
        self: Mind,
        messages: List[Dict],
        *,
        adapter_key: str = "",
        max_iterations: int = 0,
        options: Optional[dict] = None,
        tool_tags: Optional[List[str]] = None,
        allow_output_tools: bool = True,
        extra_blocked_tools: Optional[Set[str]] = None,
        completion: Optional[Dict] = None,
        require_output: bool = False,
        inherited_tools: Optional[List[Dict]] = None,
) -> str:
    """内部任务循环：正文仅内部可见，显式发送遵循任务的输出权限。

    completion 非 None 时，循环结束把结束原因写入 completion["reason"]：
    completed（正常收束）/ budget_exhausted（轮次预算用尽，产出可能只是
    中途状态）/ interrupted（协作中断）——调用方据此决策是否需要续委托。

    tool_tags 非空时按选择器加载工具集（替代默认的 "heartbeat" 标签）。
    选择器优先按 tag 匹配，同时兼容按 group 名匹配（如 mcp:web-fetch）。
    工具目录为精简常态集（always + 选择器），更多分组由模型经
    list_entity_methods / activate_tool_group 按需唤醒（自服务扩展）。
    默认允许 output 类工具（send_message/send_file 等）；任务可显式禁用。
    extra_blocked_tools 可追加屏蔽特定工具（如子代理 leaf 角色屏蔽 delegate_task）。
    require_output 要求结束前提交总结：空结束只在剩余预算内补一个禁止操作的总结轮。
    inherited_tools 保留触发轮的工具顺序，缺少的选择器工具追加到尾部；
    相同目录有利于复用缓存，执行权限仍由 blocked_tools 与统一门控约束。

    Returns:
        LLM 产出的文本内容（所有轮次输出的合并）。
    """
    self.note_activity()
    # 反思计时打点：mind.reflect 是所有反思会话（调度任务/元决策 REFLECT/
    # 子代理/手动触发）的唯一入口，在此刷新 _last_reflect_time，供
    # SituationContext.hours_since_reflect 计算真实间隔。此前该字段仅在
    # __init__ 置 0 后全仓库无写入点，hours_since_reflect 恒返回 999.0
    # 哨兵，元决策被"距上次反思 999h"误导而反复建议反思（饱和假象根因）。
    self._last_reflect_time = time.time()
    # 进行中思考计数：phase 空闲判定的活跃事实源（并发 reflect 时仅最后
    # 一个收尾的调用触发归零），异常也必须复位
    self._reflect_depth += 1
    try:
        mc = self._get_mind_config()
        safety_limit = max_iterations or mc.max_tool_iterations
        blocked_tools = build_reflect_blocklist(allow_output_tools)
        if extra_blocked_tools:
            blocked_tools = blocked_tools | set(extra_blocked_tools)

        # 每次反思会话使用唯一 scope：并行子代理/心跳 reflect 的 plan 与
        # 工具激活状态按 scope 隔离，共享字面量会互相串扰
        reflect_scope = f"reflect:{uuid.uuid4().hex[:8]}"
        active_tools, extra_selectors = await select_reflect_tools(
            self.pfc, adapter_key=adapter_key, scope=reflect_scope,
            tool_tags=tool_tags, inherited_tools=inherited_tools,
        )

        collected_text: List[str] = []
        execution_steps: List[str] = []
        output_policy = "放开外发" if allow_output_tools else "禁用外发"
        log(f"反思循环开始: {len(active_tools)} 个工具可用, 策略={output_policy}, 上限 {safety_limit} 轮", tag="思维")

        from agent.mind.think_session import think_session
        from core.tool_context import tool_request

        with think_session(self, reflect_scope, with_token=False), tool_request(reflect_scope, inherit=True):
            await self._think_loop(
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
            )

        total = "\n".join(collected_text)
        log(f"反思循环结束: 产出 {len(total)} 字", tag="思维")
        return total
    finally:
        self._reflect_depth -= 1
        self._reset_phase_if_idle()
