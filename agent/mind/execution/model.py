"""单轮模型调用、中断与失败恢复。"""


from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict, List, Optional

from agent.llm import LLMCallAborted
from agent.mind.execution.compression import _handle_overflow
from agent.mind.execution.state import ExecutionContext, RoundState
from agent.mind.execution.tool_execution import _EarlyToolRunner
from core.log import log

if TYPE_CHECKING:
    from agent.llm import ChatResult


def _streaming_enabled() -> bool:
    """流式内核开关（配置 mind_streaming_enabled，默认开）。

    流式只产生过程事件（assistant_delta），回复出口为统一频道管道；end_reply 只结束 —— 多频道语义不受影响。
    """
    try:
        from core.config import get_config_bool
        return get_config_bool("mind_streaming_enabled", True)
    except Exception:
        return True


_PROMPT_TIMEOUT = (
    "[系统通知] 本次 LLM 调用已超时（>{timeout}s），模型可能响应过慢或不可用。\n"
    "请选择以下操作之一：\n"
    "1. 调用 switch_model 切换到响应更快的模型后继续处理\n"
    "2. 调用 end_reply 结束本轮\n"
    "请立即做出选择，不要重复刚才超时的操作。"
)


async def _invoke_llm_round(
        ctx: ExecutionContext,
        state: RoundState,
        llm_messages: List[Dict],
        require_tools: bool,
        early_runner: Optional["_EarlyToolRunner"] = None,
) -> Optional[ChatResult]:
    """单次 LLM 调用；超时/上下文超限已处理（注入提示或紧急压缩）时返回 None。

    在途调用与 scope 中断事件竞争——用户刹车即刻生效（流式关闭流、
    非流式取消请求）；LLMCallAborted 返回 None，循环顶部的中断检查统一收束。
    """
    mind = ctx.mind
    on_tool_call_ready = early_runner.submit if early_runner is not None else None
    try:
        return await mind._invoke_llm_unified(
            llm_messages, ctx.active_tools or None, ctx.anything,
            tool_choice="required" if require_tools else None,
            options=ctx.options,
            purpose=ctx.mode.value,
            stream=_streaming_enabled(),
            on_delta=ctx.delta_emitter,
            on_tool_call_ready=on_tool_call_ready,
            abort_event=ctx.abort_event,
        )
    except LLMCallAborted:
        if early_runner is not None:
            early_runner.abandon(cancel=True)
        log(f"LLM 调用被用户中止 (轮次 {state.iteration + 1})，循环顶部收束", tag="中断")
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: LLM 调用被用户中止")
        return None
    except asyncio.TimeoutError:
        timeout_val = mind._get_mind_config().llm_timeout
        log(f"LLM 调用超时 ({timeout_val}s)，注入恢复提示继续循环", "WARNING", tag="思维")
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: LLM 调用超时 ({timeout_val}s)")
        ctx.tool_chain.append({
            "role": "system",
            "content": _PROMPT_TIMEOUT.format(timeout=timeout_val),
            "_source": {"origin": "timeout_recovery"},
        })
        state.iteration += 1
        return None
    except Exception as exc:
        # 上下文超限：立即压缩后重试（连续压缩无效时放弃，防止死循环）
        if await _handle_overflow(ctx, state, exc):
            return None
        raise
