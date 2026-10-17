"""思维执行中的压缩触发、记忆沉淀与文件状态恢复。"""


from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol

from agent.mind.execution.state import ExecutionContext, RoundState
from core.latebind import LateBinding
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


class FileStateCacheView(Protocol):
    """rehydration 消费的文件状态缓存最小接口（entities 侧实现，结构化匹配）。"""

    def recent_entries(self, n: int) -> List[Any]:
        """最近使用的 n 条 (key, state) 对（新→旧）。"""
        ...  # Protocol 方法体


file_state_cache_port: LateBinding[Callable[[str], FileStateCacheView]] = LateBinding(
    "mind.file_state_cache",
)


async def _precompact_flush(mind: "Mind", current_scope: str) -> bool:
    """上下文压缩前抢跑记忆提取（PreCompact flush）。

    压缩只裁剪内存中的消息链；被裁掉的细节若尚未经 auto_capture 沉淀，
    本会话后续轮次就无法召回。抢在压缩前把该 scope 的待定对话提取为
    长期记忆（信息不失帧）。实际执行了提取返回 True。

    fail-open：异常/超时仅记日志与指标，绝不阻断压缩；
    只写记忆 DB，不触碰任何 prompt 分层内容（不影响缓存前缀）。
    """
    try:
        from core.config import get_config_bool
        if not get_config_bool("memory_precompact_flush_enabled", True):
            return False
    except Exception:
        pass
    if not current_scope:
        return False
    # think_loop 的 scope 是 entity_scope 形态（user_qq:123），
    # auto_capture 游标键为 user:qq:123
    scope_key = current_scope.replace("_", ":", 1)
    if not scope_key.startswith(("user:", "group:")):
        return False
    from agent.memory import metrics
    timeout = 20.0
    try:
        from core.config import get_config_float
        timeout = max(1.0, get_config_float("memory_precompact_flush_timeout", 20.0))
    except Exception:
        pass
    try:
        from agent.memory.auto_capture import flush_scope_capture
        done = await asyncio.wait_for(flush_scope_capture(mind, scope_key), timeout=timeout)
    except asyncio.TimeoutError:
        metrics.incr("capture.precompact_flush_timeout")
        log(f"压缩前记忆提取超时（{timeout:.0f}s），直接压缩", "WARNING", tag="压缩")
        return False
    except Exception as exc:
        metrics.incr("capture.precompact_flush_failed")
        log(f"压缩前记忆提取失败（不阻断压缩）: {exc}", "WARNING", tag="压缩")
        return False
    metrics.incr("capture.precompact_flush" if done else "capture.precompact_flush_skipped")
    if done:
        log(f"压缩前记忆提取完成 [{scope_key}]", tag="压缩")
    return bool(done)


async def _compress_context(
        mind: "Mind",
        base_messages: List[Dict],
        tool_chain: List[Dict],
        scope: str,
        tools: Optional[List[Dict]] = None,
) -> tuple[List[Dict], List[Dict]]:
    """执行上下文压缩（保头保尾 + 中间摘要），返回新的 (base_messages, tool_chain)。

    压缩成败记录到熔断器（连续失败 3 次停止尝试）；
    成功后执行 rehydration：重读压缩前正在处理的文件，恢复工作现场
    （压缩后关键文件内容再水化，消费 file_state 缓存）。

    tools: 当前激活工具 schema 数组，传入时启用前缀复用摘要
    （摘要调用复用主对话前缀命中 KV 缓存）。
    """
    try:
        new_base, new_chain = await mind.compressor.compress_messages(
            base_messages, tool_chain,
            scope=scope,
            summarizer=mind.summarize_text,
            tools=tools,
        )
    except Exception as exc:
        mind.compressor._record_compress_result(False)
        log(f"上下文压缩失败: {exc}", "WARNING", tag="压缩")
        raise
    mind.compressor._record_compress_result(True)
    # 压缩整体重写前缀 = 已知断裂：登记后下次校验重建基线（不误报漂移），
    # 快照/命中率列表据因标识"压缩"而非"平台波动"
    from agent.context.prefix_guard import prefix_guard
    prefix_guard.note_legal_break(scope, "compress")
    rehydrated = await asyncio.to_thread(_rehydrate_recent_files, scope)
    if rehydrated:
        new_chain = [*new_chain, {"role": "system", "content": rehydrated,
                                  "_source": {"origin": "rehydration"}}]
    return new_base, new_chain


_REHYDRATE_MAX_FILES = 5


_REHYDRATE_PER_FILE_CHARS = 5000


_REHYDRATE_TOTAL_CHARS = 50000


def _rehydrate_recent_files(scope: str) -> str:
    """压缩后重读最近读取/编辑过的文件（≤5 个），生成恢复上下文。"""
    try:
        if not file_state_cache_port.bound:
            return ""
        cache = file_state_cache_port.get()(scope)
        entries = cache.recent_entries(_REHYDRATE_MAX_FILES)
    except Exception:
        return ""
    if not entries:
        return ""
    import os
    sections: List[str] = []
    total = 0
    for path, _state in entries:  # 最近使用的优先
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_REHYDRATE_PER_FILE_CHARS + 1)
        except OSError:
            continue
        if len(content) > _REHYDRATE_PER_FILE_CHARS:
            content = content[:_REHYDRATE_PER_FILE_CHARS] + "\n... (截断)"
        block = f"### {os.path.basename(path)} ({path})\n```\n{content}\n```"
        if total + len(block) > _REHYDRATE_TOTAL_CHARS:
            break
        sections.append(block)
        total += len(block)
    if not sections:
        return ""
    return (
        "[系统] 上下文已压缩。以下是你压缩前正在处理的文件的最新内容"
        "（自动恢复，供继续工作参考；如需编辑请遵循 read-before-write 流程）：\n"
        + "\n\n".join(sections)
    )


async def _handle_overflow(
        ctx: ExecutionContext,
        state: RoundState,
        exc: Exception,
) -> bool:
    """上下文溢出压缩路径：LLM 上下文超限时紧急压缩后重试。

    已压缩（可安全重试）返回 True；非溢出错误或连续压缩无效返回 False，
    由主循环原样抛出（连续压缩无效时放弃，防止死循环）。
    """
    from agent.llm.resilience import classify_llm_error

    mind = ctx.mind
    classified = classify_llm_error(exc)
    if not (
        classified.should_compress
        and mind.compressor is not None
        and state.consecutive_overflow_compressions < 2
    ):
        return False
    state.consecutive_overflow_compressions += 1
    log(
        f"LLM 上下文超限，执行紧急压缩 (第 {state.consecutive_overflow_compressions} 次)",
        "WARNING", tag="压缩",
    )
    async with mind.compressor.scope_lock(ctx.current_scope):
        ctx.base_messages, ctx.tool_chain = await _compress_context(
            mind, ctx.base_messages, ctx.tool_chain, ctx.current_scope,
            tools=ctx.active_tools,
        )
    # 紧急压缩后旧真用量已失真，清零防止下轮误判再次溢出
    state.last_input_tokens = 0
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 上下文超限，已紧急压缩")
    state.iteration += 1
    return True
