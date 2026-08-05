"""Mind LLM 调用块：统一调用入口、重试/回退、流式聚合与选项合并。

函数以 mind 实例为第一参数（与 agent.mind.tools.* 同风格），
Mind 类持有一行薄委托，调用方签名零变化。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.llm import ChatResult
from agent.llm.llm_client import LLMClient
from core.event_bus import (
    EVENT_THINKING_LLM_END,
    EVENT_THINKING_LLM_START,
    event_bus,
)
from core.log import log

if TYPE_CHECKING:
    from types import ModuleType

    from agent.messages import Everything
    from agent.mind.mind import Mind


def _mind_module() -> "ModuleType":
    """延迟引用 agent.mind.mind 模块。

    normalize_for_send / context_audit 经模块属性访问，
    保持 tests 对 agent.mind.mind.* 的 monkeypatch 语义（且避免循环导入）。
    """
    import agent.mind.mind as mind_module
    return mind_module


def get_model_context_length(mind: "Mind") -> int:
    """获取当前模型的上下文窗口（tokens，带缓存；0 表示未知）。

    缓存键含模型名，switch_model 后自动失效。
    """
    llm_client = mind.llm if isinstance(mind.llm, LLMClient) else None
    if llm_client is None:
        return 0
    current_model = llm_client.config.litellm_model or ""
    if mind._cached_context_length > 0 and mind._cached_model_name == current_model:
        return mind._cached_context_length
    max_ctx = 0
    try:
        info = LLMClient.get_model_info(current_model)
        max_ctx = info.get("max_input_tokens") or info.get("max_tokens") or 0
    except Exception:
        max_ctx = 0
    if not max_ctx:
        max_ctx = llm_client.config.context_window or 0
    mind._cached_context_length = max_ctx
    mind._cached_model_name = current_model
    return max_ctx


async def _invoke_llm_unified(
        mind: "Mind",
        messages: List[Dict],
        tools: Optional[list[dict]],
        anything: Optional["Everything"] = None,
        *,
        tool_choice: Optional[str] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
        on_delta: Optional[Any] = None,
) -> ChatResult:
    """统一 LLM 调用（带重试、模型回退和事件追踪）。

    stream=True 时优先走主客户端流式（增量经 on_delta 上报），
    流式失败自动回退非流式重试路径（行为与非流式完全一致）。
    """
    model_name = mind.llm.config.model if isinstance(mind.llm, LLMClient) else "unknown"
    _mm = _mind_module()

    # 上下文快照捕获（normalize 前，_layer 标签尚存；未布防时零开销）
    from agent.mind.context_snapshot import context_snapshot
    await context_snapshot.try_capture(messages, tools, model_name)

    # 发送边界统一规整（message_schema.normalize_for_send）：
    # 角色归一（头部提示词分层保持 system 供 Anthropic 前缀缓存，中途注入
    # 转 user 保留位置语义）+ 尾部 assistant prefill 修复
    messages = _mm.normalize_for_send(messages)
    log(f"调用 LLM: {model_name} msgs={len(messages)}", tag="思维")
    tool_names = [t.get("function", {}).get("name", "") for t in (tools or [])]
    await event_bus.emit(EVENT_THINKING_LLM_START, {
        "model": model_name,
        "message_count": len(messages),
        "tool_count": len(tools) if tools else 0,
        "tool_names": tool_names[:20],
    })
    t0 = time.time()
    try:
        if stream:
            try:
                result = await mind._llm_chat_stream_once(
                    messages, tools,
                    tool_choice=tool_choice, options=options, on_delta=on_delta,
                )
            except Exception as stream_exc:
                log(f"流式调用失败，回退非流式: {stream_exc}", "DEBUG", tag="思维")
                result = await mind._llm_chat_with_retry(
                    messages, tools, tool_choice=tool_choice, options=options,
                )
        else:
            result = await mind._llm_chat_with_retry(messages, tools, tool_choice=tool_choice, options=options)
    except Exception as exc:
        # 关闭链路中的 LLM 节点，避免一直停留在执行中
        await event_bus.emit(EVENT_THINKING_LLM_END, {
            "model": model_name,
            "duration_ms": round((time.time() - t0) * 1000),
            "error": str(exc),
            "success": False,
        })
        # 请求级审计：异常交换同样落盘（未开启时零开销）
        await _mm.context_audit.record_exchange(
            model=model_name, messages=messages, tools=tools,
            error=exc, duration_ms=(time.time() - t0) * 1000,
        )
        raise
    elapsed_ms = (time.time() - t0) * 1000
    # 请求级审计：规整后最终发送的 messages + 完整响应（未开启时零开销）
    await _mm.context_audit.record_exchange(
        model=result.model or model_name, messages=messages, tools=tools,
        result=result, duration_ms=elapsed_ms,
    )
    mc = mind._get_mind_config()
    if mc.log_ai_output:
        if result.reasoning_content:
            log(f"AI 推理: {result.reasoning_content[:300]}", "DEBUG", tag="思维")
        if result.content:
            log(f"AI 输出: {result.content[:500]}", tag="思维")
    usage_data: Dict = {}
    max_ctx = 0
    if result.usage:
        usage_data = {
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
            "cache_read_input_tokens": result.usage.cache_read_input_tokens,
            "cache_creation_input_tokens": result.usage.cache_creation_input_tokens,
            "cache_hit_rate": round(result.usage.cache_hit_rate, 4),
        }
        from agent.mind.cache_stats import cache_usage_tracker
        cache_usage_tracker.record(result.usage)
        log(
            f"LLM 用量: prompt={result.usage.prompt_tokens} "
            f"cache_read={result.usage.cache_read_input_tokens} "
            f"cache_creation={result.usage.cache_creation_input_tokens}",
            "DEBUG", tag="思维",
        )
        llm_client = mind.llm if isinstance(mind.llm, LLMClient) else None
        if llm_client:
            # 复用模型名缓存的上下文窗口查询，避免每次 LLM 调用重复 get_model_info
            max_ctx = mind.get_model_context_length()
    else:
        # usage 缺失埋点：流式端点/网关未返回 usage 时缓存统计不可用
        from agent.mind.cache_stats import cache_usage_tracker
        cache_usage_tracker.record_missing()
        log(
            f"LLM 调用未返回 usage（{result.model or model_name}），"
            "缓存命中统计不可用；若为流式调用，端点可能未支持 include_usage",
            "DEBUG", tag="思维",
        )
    usage_percent: Optional[float] = None
    if usage_data.get("total_tokens") and max_ctx > 0:
        usage_percent = round(usage_data["total_tokens"] / max_ctx * 100, 1)
    await event_bus.emit(EVENT_THINKING_LLM_END, {
        "model": result.model or model_name,
        "duration_ms": round(elapsed_ms),
        "has_content": bool(result.content),
        "content_preview": (result.content or "")[:200],
        "tool_calls": [tc.name for tc in result.tool_calls] if result.tool_calls else [],
        "has_reasoning": bool(result.reasoning_content),
        "reasoning_preview": (result.reasoning_content or "")[:800],
        "usage": usage_data,
        "usage_percent": usage_percent,
        "max_tokens": max_ctx,
    })
    return result


def _merge_llm_options(mind: "Mind", options: Optional[dict]) -> dict:
    """合并 LLM 调用选项：全局 reasoning_effort 兜底 + 会话级参数覆盖。"""
    mc = mind._get_mind_config()
    merged_options = dict(options or {})
    if mc.reasoning_effort and "reasoning_effort" not in merged_options:
        from agent.llm.reasoning import normalize_effort
        # 全局配置容错：非法值归一为空，避免污染每一次 LLM 调用
        effort = normalize_effort(mc.reasoning_effort)
        if effort:
            merged_options["reasoning_effort"] = effort
    if mind._session_llm_params:
        merged_options.update(mind._session_llm_params)
    return merged_options


async def _llm_chat_with_retry(
        mind: "Mind",
        messages: List[Dict],
        tools: Optional[list[dict]],
        *,
        tool_choice: Optional[str] = None,
        options: Optional[dict] = None,
) -> ChatResult:
    mc = mind._get_mind_config()
    merged_options = _merge_llm_options(mind, options)

    model_override_id = merged_options.pop("_model_id", None)
    final_options = merged_options or None

    if mind.llm_manager:
        if model_override_id:
            # 执行路径：覆盖模型不存在或已停用时回退默认
            primary = mind.llm_manager.get_enabled_client(model_override_id)
            if not primary:
                from core.log import log
                log(f"指定模型 '{model_override_id}' 不可用（不存在或已停用），回退到默认模型", "WARNING", tag="思维")
                primary = mind.llm if isinstance(mind.llm, LLMClient) else None
        else:
            primary = mind.llm if isinstance(mind.llm, LLMClient) else None
        if primary is not None and not primary.config.enabled:
            primary = mind.llm_manager.get_default()
        result = await mind.llm_manager.chat_with_fallback(
            messages,
            options=final_options,
            tools=tools,
            tool_choice=tool_choice,
            client=primary,
            max_retries=mc.llm_max_retries,
            timeout=mc.llm_timeout,
        )
    else:
        result = await asyncio.wait_for(
            mind.llm.chat(messages, options=final_options, tools=tools, tool_choice=tool_choice),
            timeout=mc.llm_timeout,
        )
    if result.content:
        from core.tags import rm_unless_text
        result.content = rm_unless_text(result.content)
    return result


async def _llm_chat_stream_once(
        mind: "Mind",
        messages: List[Dict],
        tools: Optional[list[dict]],
        *,
        tool_choice: Optional[str] = None,
        options: Optional[dict] = None,
        on_delta: Optional[Any] = None,
) -> ChatResult:
    """主客户端单次流式调用，增量经 on_delta 上报，聚合为 ChatResult 返回。

    多频道语义约束：流式只产生过程事件，回复出口仍是 send_message/end_reply。
    失败由调用方回退到 _llm_chat_with_retry（完整降级链），本函数不重试。
    """
    if not isinstance(mind.llm, LLMClient):
        raise RuntimeError("当前 LLM 客户端不支持流式调用")
    mc = mind._get_mind_config()
    merged_options = _merge_llm_options(mind, options)
    merged_options.pop("_model_id", None)
    final_options = merged_options or None

    from agent.llm.types import ChatResult as _ChatResult

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: List[Any] = []
    usage = None
    finish_reason = ""
    stream_iter = mind.llm.chat_stream(
        messages, options=final_options, tools=tools, tool_choice=tool_choice,
    ).__aiter__()
    while True:
        try:
            # 每个 chunk 独立计时（停滞流保护；asyncio.timeout 需 3.11+，用 wait_for 兼容 3.10）
            delta = await asyncio.wait_for(stream_iter.__anext__(), timeout=mc.llm_timeout)
        except StopAsyncIteration:
            break
        if delta.content:
            content_parts.append(delta.content)
            if on_delta is not None:
                await on_delta(delta.content, False)
        if delta.reasoning_content:
            reasoning_parts.append(delta.reasoning_content)
            if on_delta is not None:
                await on_delta(delta.reasoning_content, True)
        if delta.tool_calls:
            tool_calls.extend(delta.tool_calls)
        if delta.usage is not None:
            usage = delta.usage
        if delta.finish_reason:
            finish_reason = delta.finish_reason
    content = "".join(content_parts)
    if content:
        from core.tags import rm_unless_text
        content = rm_unless_text(content)
    return _ChatResult(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        reasoning_content="".join(reasoning_parts),
        usage=usage,
        model=mind.llm.config.model,
    )
