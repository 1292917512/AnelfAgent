"""Mind LLM 调用块：统一调用入口、重试/回退、流式聚合与选项合并。

函数以 mind 实例为第一参数（与 agent.mind.tools.* 同风格），
Mind 类持有一行薄委托，调用方签名零变化。
"""

from __future__ import annotations

import asyncio
import sys
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

# asyncio.timeout 需 3.11+（无 per-chunk Task 创建开销）；3.10 回退 wait_for
_HAS_ASYNCIO_TIMEOUT = sys.version_info >= (3, 11)


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
        purpose: str = "reply",
) -> ChatResult:
    """统一 LLM 调用（带重试、模型回退和事件追踪）。

    stream=True 时优先走主客户端流式（增量经 on_delta 上报），
    流式失败自动回退非流式重试路径（行为与非流式完全一致）。
    """
    model_name = mind.llm.config.model if isinstance(mind.llm, LLMClient) else "unknown"
    _mm = _mind_module()

    # 前缀稳定性守卫（normalize 前，_layer 标签尚存）：对前缀层消息逐条哈希
    # 与同 scope 上一次调用比对，首个不一致位置即缓存断裂点。仅观测不阻断，
    # 归因随快照落盘 records.jsonl（对齐 dsh 运行时不变式的"独立校验前缀"
    # 思想，适配为轻量观测版）。须先于 try_capture 计算，归因才能并入本次快照。
    from agent.mind.prefix_guard import prefix_guard
    # scope 解析链：消息实体 scope（主回复）> 委托链绑定的父会话 scope
    # （子代理 reflect，见 delegation_manager.bind_usage_scope）> 激活上下文
    # scope。reflect 的一次性 scope 落到第三档时会被 scope_usage 丢弃
    # （防孤儿行挤爆容量），前两档保证用量归属正确。
    _guard_scope = ""
    if anything is not None:
        _guard_scope = getattr(anything, "entity_scope", "") or ""
    if not _guard_scope:
        try:
            from agent.mind.scope_usage import current_usage_scope
            _guard_scope = current_usage_scope()
        except Exception:
            _guard_scope = ""
    if not _guard_scope:
        try:
            from agent.mind.tool_activation import ToolActivationManager
            _guard_scope = ToolActivationManager.current_scope()
        except Exception:
            _guard_scope = ""
    prefix_drift = (
        prefix_guard.check(_guard_scope, messages, kind=purpose)
        if _guard_scope else None
    )

    # 上下文快照捕获（normalize 前，_layer 标签尚存；未布防时零开销）
    # kind=调用用途（reply/reflect…）：列表行按用途区分主对话与任务调用，
    # 任务首轮的结构性低命中不再被误读为主对话缓存故障
    from agent.mind.context_snapshot import context_snapshot
    await context_snapshot.try_capture(
        messages, tools, model_name, kind=purpose, prefix_drift=prefix_drift,
    )

    # 缓存断点装饰（唯一装饰点，_layer 标签尚存时按锚点表放置；
    # 按主客户端线型判定，回退候选的供应商适配在 llm_manager）
    from agent.llm.prompt_cache import decorate_messages, is_anthropic_wire
    _llm = getattr(mind, "llm", None)
    _llm_cfg = getattr(_llm, "config", None)
    messages = decorate_messages(messages, anthropic=is_anthropic_wire(
        getattr(_llm_cfg, "litellm_model", "") or "",
        getattr(_llm_cfg, "api_type", "") or "",
    ), api_type=getattr(_llm_cfg, "api_type", "") or "")

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
                # 已下发的增量文本在重试全量文本到达后会重复显示，先通知通道重置
                reset_fn = getattr(on_delta, "reset", None) if on_delta is not None else None
                if reset_fn is not None:
                    try:
                        await reset_fn()
                    except Exception:
                        pass  # 重置通知失败不影响回退
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
        cache_usage_tracker.record(result.usage, kind=purpose, model=result.model or model_name)
        # 会话级用量累计（成本账本；fail-open，scope 取 prefix_guard 已算出的值）
        if _guard_scope:
            from agent.mind.scope_usage import scope_usage_stats
            scope_usage_stats.record(_guard_scope, purpose, result.usage)
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
        cache_usage_tracker.record_missing(kind=purpose)
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
        # TTFT（流式路径）：排队/首 token 延迟，与 duration_ms（总时长）
        # 相减即输出生成耗时——两个独立的延迟来源分别可诊断
        "ttft_ms": round(result.ttft_ms) if result.ttft_ms is not None else None,
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
    """合并 LLM 调用选项：调用方 options + 会话级参数覆盖。

    思考等级不做全局兜底——是否思考由选用的模型自己决定（模型配置的
    reasoning_effort / thinking 契约），对话层不统一注入档位。
    """
    merged_options = dict(options or {})
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
    ttft_ms: Optional[float] = None
    started = time.monotonic()
    stream_iter = mind.llm.chat_stream(
        messages, options=final_options, tools=tools, tool_choice=tool_choice,
    ).__aiter__()
    while True:
        try:
            # 每个 chunk 独立计时（停滞流保护）。3.11+ 用 asyncio.timeout
            # （无 per-chunk Task 创建开销）；3.10 回退 wait_for
            if _HAS_ASYNCIO_TIMEOUT:
                async with asyncio.timeout(mc.llm_timeout):
                    delta = await stream_iter.__anext__()
            else:
                delta = await asyncio.wait_for(stream_iter.__anext__(), timeout=mc.llm_timeout)
        except StopAsyncIteration:
            break
        if ttft_ms is None:
            ttft_ms = (time.monotonic() - started) * 1000
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
        ttft_ms=ttft_ms,
    )
