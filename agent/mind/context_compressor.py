"""上下文压缩管线（参考 hermes-agent context_compressor / conversation_compression）。

自动检测上下文溢出风险，智能压缩中间轮次，保留关键信息，延长对话寿命：

- 溢出检测：优先使用上轮真实 prompt_tokens，否则按 chars/4 估算；
  阈值 = (context_length - max_output) × threshold_percent（小窗口退化 0.85）
- 压缩策略：保头（system 层 + 首轮）保尾（最近 N 条），中间轮次由 LLM 生成结构化摘要
- 关键信息保护：未完成任务、用户偏好、关键实体/记忆 ID、重要决定
- 压缩反馈：注入元消息告知 AI "以下是之前对话的摘要"
- 压缩后使 Prompt 层缓存失效（volatile 层内容已变化）
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.log import log

_CHARS_PER_TOKEN = 4
_SMALL_WINDOW_TOKENS = 32_000
_SMALL_WINDOW_FALLBACK_RATIO = 0.85


@dataclass
class CompressionConfig:
    """压缩配置。"""

    enabled: bool = True
    threshold_percent: float = 0.75
    protect_first_n: int = 2
    protect_last_n: int = 10
    summary_max_chars: int = 2000
    min_compressible: int = 8

    @classmethod
    def from_config_manager(cls) -> "CompressionConfig":
        from core.config import get_config_bool, get_config_float, get_config_int
        return cls(
            enabled=get_config_bool("compression_enabled", True),
            threshold_percent=get_config_float("compression_threshold_percent", 0.75),
            protect_first_n=get_config_int("compression_protect_first_n", 2),
            protect_last_n=get_config_int("compression_protect_last_n", 10),
            summary_max_chars=get_config_int("compression_summary_max_chars", 2000),
        )


@dataclass
class CompressionMetrics:
    """压缩指标（供可观测性使用）。"""

    total_compressions: int = 0
    last_before_tokens: int = 0
    last_after_tokens: int = 0
    last_ratio: float = 0.0
    last_at: float = 0.0
    failures: int = 0

    def record(self, before: int, after: int) -> None:
        self.total_compressions += 1
        self.last_before_tokens = before
        self.last_after_tokens = after
        self.last_ratio = round(after / before, 3) if before else 0.0
        self.last_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_compressions": self.total_compressions,
            "last_before_tokens": self.last_before_tokens,
            "last_after_tokens": self.last_after_tokens,
            "last_ratio": self.last_ratio,
            "last_at": self.last_at,
            "failures": self.failures,
        }


_SUMMARY_PROMPT = """请将以下对话片段压缩为结构化摘要。必须保留：
1. 未完成的任务、计划与待办事项
2. 用户明确表达的偏好与要求
3. 关键实体（人名、用户ID、群组ID）与记忆 ID
4. 重要的决定、结论与事实
5. 最近使用的工具及其关键结果

要求：分点列出，简洁准确，不超过 {max_chars} 字。直接输出摘要内容，不要额外解释。

[待压缩对话]
{conversation}"""


class ContextCompressor:
    """上下文压缩器：检测溢出并压缩中间轮次。"""

    def __init__(self, mind: Any, config: Optional[CompressionConfig] = None) -> None:
        self._mind = mind
        self.config = config or CompressionConfig.from_config_manager()
        self.metrics = CompressionMetrics()
        # 手动压缩请求（compress_context 工具设置，think_loop 消费）
        self._manual_requests: set[str] = set()

    # ------------------------------------------------------------------
    # 溢出检测
    # ------------------------------------------------------------------

    def threshold_tokens(self) -> int:
        """计算压缩触发阈值（参考 hermes：输出预留从窗口扣除，小窗口退化 85%）。"""
        context_length = self._mind.get_model_context_length()
        if context_length <= 0:
            return 0
        effective = context_length
        threshold = int(effective * self.config.threshold_percent)
        if context_length <= _SMALL_WINDOW_TOKENS and threshold >= effective * 0.9:
            return int(effective * _SMALL_WINDOW_FALLBACK_RATIO)
        return threshold

    @staticmethod
    def estimate_tokens(messages: List[Dict]) -> int:
        """按 chars/4 粗略估算消息列表的 token 数。"""
        total_chars = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                total_chars += sum(
                    len(str(part.get("text", ""))) for part in content if isinstance(part, dict)
                )
            for tc in msg.get("tool_calls") or []:
                try:
                    total_chars += len(json.dumps(tc, ensure_ascii=False))
                except (TypeError, ValueError):
                    pass
        return total_chars // _CHARS_PER_TOKEN

    def should_compress(
            self,
            messages: List[Dict],
            *,
            last_prompt_tokens: int = 0,
            scope: str = "",
    ) -> bool:
        """判断是否需要压缩（真实用量优先，估算兜底；支持手动请求）。"""
        if not self.config.enabled:
            return False
        if scope and scope in self._manual_requests:
            return True
        threshold = self.threshold_tokens()
        if threshold <= 0:
            return False
        tokens = last_prompt_tokens or self.estimate_tokens(messages)
        return tokens >= threshold

    def request_manual(self, scope: str) -> None:
        """请求手动压缩（下一轮 LLM 调用前生效）。"""
        self._manual_requests.add(scope)
        log(f"手动压缩请求已登记: scope={scope}", tag="压缩")

    # ------------------------------------------------------------------
    # 压缩执行
    # ------------------------------------------------------------------

    async def compress_messages(
            self,
            base_messages: List[Dict],
            tool_chain: List[Dict],
            *,
            scope: str = "",
            summarizer: Optional[Callable[[str], Any]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """压缩上下文：保头保尾，中间轮次生成摘要。

        Args:
            base_messages: 基础上下文（system 层 + volatile + 对话历史）
            tool_chain: 当前会话的工具调用链
            scope: 对话 scope（用于缓存失效与手动请求消费）
            summarizer: 摘要生成函数（默认用 mind.llm_chat）

        Returns:
            (新的 base_messages, 新的 tool_chain)
        """
        self._manual_requests.discard(scope)
        before_tokens = self.estimate_tokens(base_messages + tool_chain)

        # 1. 分离头部 system 消息（stable/context/volatile 层整体保护）
        head_system: List[Dict] = []
        rest: List[Dict] = []
        for msg in base_messages:
            if msg.get("role") == "system" and not rest:
                head_system.append(msg)
            else:
                rest.append(msg)
        compressible = rest + tool_chain

        if len(compressible) < self.config.min_compressible:
            log("可压缩消息不足，跳过压缩", "DEBUG", tag="压缩")
            return base_messages, tool_chain

        # 2. 保头保尾
        first_n = self.config.protect_first_n
        last_n = self.config.protect_last_n
        if len(compressible) <= first_n + last_n:
            first_n = max(0, (len(compressible) - last_n) // 2)
        head = compressible[:first_n]
        tail = compressible[len(compressible) - last_n:] if last_n else []
        middle = compressible[first_n:len(compressible) - last_n if last_n else None]

        if not middle:
            log("中间轮次为空，无需压缩", "DEBUG", tag="压缩")
            return base_messages, tool_chain

        # 3. 生成摘要（LLM 失败时回退确定性摘要）
        summary = await self._summarize(middle, summarizer)

        # 4. 清理尾部孤儿 tool 消息（其 assistant 调用已被压缩）
        tail = self._sanitize_tail(tail)

        # 5. 重组：头部 system + 保首轮 + 压缩反馈 + 保尾轮
        feedback = {
            "role": "system",
            "content": (
                f"[上下文压缩] 为节省上下文空间，之前 {len(middle)} 条对话已压缩为以下摘要。"
                "其中包含未完成任务与关键信息，请基于摘要继续：\n"
                f"{summary}"
            ),
        }
        new_base = head_system + head + [feedback]
        new_chain = tail

        after_tokens = self.estimate_tokens(new_base + new_chain)
        self.metrics.record(before_tokens, after_tokens)
        log(
            f"上下文压缩完成: {before_tokens} -> {after_tokens} tokens "
            f"(压缩 {len(middle)} 条, 比率 {self.metrics.last_ratio})",
            tag="压缩",
        )

        # 6. 压缩后使 Prompt 层缓存失效（volatile 层内容已变化）
        try:
            from agent.mind.prompt_layers import prompt_cache_manager
            prompt_cache_manager.invalidate(scope)
        except Exception:
            pass

        return new_base, new_chain

    async def _summarize(
            self,
            middle: List[Dict],
            summarizer: Optional[Callable[[str], Any]],
    ) -> str:
        """生成中间轮次摘要（LLM 优先，失败回退确定性拼接）。"""
        if summarizer is None:
            summarizer = getattr(self._mind, "summarize_text", None)
        conversation_text = self._render_for_summary(middle)
        try:
            if summarizer is not None:
                result = summarizer(
                    _SUMMARY_PROMPT.format(
                        max_chars=self.config.summary_max_chars,
                        conversation=conversation_text,
                    )
                )
                if hasattr(result, "__await__"):
                    result = await result
                text = getattr(result, "content", None) or str(result or "")
                text = text.strip()
                if text:
                    return text[: self.config.summary_max_chars]
        except Exception as exc:
            self.metrics.failures += 1
            log(f"压缩摘要生成失败，回退确定性摘要: {exc}", "WARNING", tag="压缩")
        return self._fallback_summary(middle)

    @staticmethod
    def _render_for_summary(messages: List[Dict]) -> str:
        """将消息渲染为摘要输入文本（截断超长内容）。"""
        lines: List[str] = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content")
            if isinstance(content, str) and content:
                lines.append(f"[{role}] {content[:500]}")
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                lines.append(f"[tool_call] {fn.get('name', '?')}({str(fn.get('arguments', ''))[:200]})")
        return "\n".join(lines)

    @staticmethod
    def _fallback_summary(messages: List[Dict]) -> str:
        """确定性摘要：提取用户消息与工具名（无 LLM 时的兜底）。"""
        user_lines = [
            (msg.get("content") or "")[:200]
            for msg in messages
            if msg.get("role") == "user" and isinstance(msg.get("content"), str)
        ]
        tool_names = [
            (tc.get("function", {}) or {}).get("name", "")
            for msg in messages
            for tc in (msg.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        parts: List[str] = []
        if user_lines:
            parts.append("用户消息要点:\n" + "\n".join(f"- {line}" for line in user_lines[:10]))
        if tool_names:
            parts.append(f"已调用工具: {', '.join(dict.fromkeys(tool_names))}")
        return "\n".join(parts) or "（早期对话已省略）"

    @staticmethod
    def _sanitize_tail(tail: List[Dict]) -> List[Dict]:
        """清理尾部孤儿 tool 消息（其 assistant 调用已被压缩掉）。"""
        cleaned = list(tail)
        while cleaned and cleaned[0].get("role") == "tool":
            cleaned.pop(0)
        return cleaned


# ------------------------------------------------------------------
# AI 可调用工具：手动触发压缩
# ------------------------------------------------------------------

from entities._sdk import deferred_tool  # noqa: E402

_compressor_ref: Optional[ContextCompressor] = None


def register_compressor(compressor: ContextCompressor) -> None:
    """注册当前 Mind 的压缩器（Mind 初始化时调用）。"""
    global _compressor_ref
    _compressor_ref = compressor


def get_compressor() -> Optional[ContextCompressor]:
    """获取当前压缩器实例。"""
    return _compressor_ref


@deferred_tool(
    name="compress_context",
    group="thinking", tags=["always"], source="mind.core",
    description="手动触发上下文压缩：将早期对话压缩为摘要以释放上下文空间。"
    "当感觉对话变长、响应变慢或收到上下文溢出提示时使用。",
)
def _compress_context_tool(reason: str = "") -> str:
    """手动触发上下文压缩。

    Args:
        reason: 触发原因（仅日志记录）
    """
    compressor = get_compressor()
    if compressor is None:
        return json.dumps({"error": "压缩器未初始化"}, ensure_ascii=False)
    try:
        from agent.mind.tool_activation import ToolActivationManager
        scope = ToolActivationManager.current_scope()
    except Exception:
        scope = ""
    compressor.request_manual(scope)
    if reason:
        log(f"AI 请求手动压缩: {reason}", tag="压缩")
    return json.dumps({
        "ok": True,
        "message": "压缩请求已登记，将在下一轮 LLM 调用前执行。",
    }, ensure_ascii=False)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_COMPRESSION_CONFIGS = {
    "上下文压缩": {
        "compression_enabled": {
            "description": "是否启用上下文自动压缩",
            "default": True,
        },
        "compression_threshold_percent": {
            "description": "压缩触发阈值（占模型上下文窗口比例）",
            "default": 0.75,
        },
        "compression_protect_first_n": {
            "description": "压缩时保留的首部消息数",
            "default": 2,
        },
        "compression_protect_last_n": {
            "description": "压缩时保留的尾部消息数",
            "default": 10,
        },
        "compression_summary_max_chars": {
            "description": "压缩摘要最大字符数",
            "default": 2000,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_COMPRESSION_CONFIGS)
