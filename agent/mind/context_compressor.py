"""上下文压缩管线（参考 hermes-agent context_compressor / conversation_compression）。

自动检测上下文溢出风险，智能压缩中间轮次，保留关键信息，延长对话寿命：

- 溢出检测：优先使用上轮真实 prompt_tokens，否则本地估算
  （tiktoken cl100k_base 真实分词，缺失/异常时回退 chars/4）；
  阈值 = (context_length - max_output) × threshold_percent（小窗口退化 0.85）
- 压缩策略：保头（system 层 + 首轮）保尾（最近 N 条），中间轮次由 LLM 生成结构化摘要
- 用户原话保护：中间段带到达元数据标签的真 user 消息原文保留不压（参考
  Mini-Agent），摘要有损二次转述不适用于用户的承诺/偏好/情感表达；
  机器生成的 user 角色消息（proactive 指令/prefill 修复的独白）仍随摘要压缩
- 关键信息保护：未完成任务、用户偏好、关键实体/记忆 ID、重要决定
- 压缩反馈：注入元消息告知 AI "以下是之前对话的摘要"
- 压缩后使 Prompt 层缓存失效（volatile 层内容已变化）
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.log import log

_CHARS_PER_TOKEN = 4
_TOKENS_PER_MESSAGE = 4  # 每条消息的结构开销（参考 Mini-Agent / OpenAI 计数惯例）
_SMALL_WINDOW_TOKENS = 32_000
_SMALL_WINDOW_FALLBACK_RATIO = 0.85

# tiktoken 惰性加载（模块级缓存；缺失或初始化失败时回退 chars/4 估算）
_TIKTOKEN_ENC: Any = None
_TIKTOKEN_TRIED: bool = False


def _get_tiktoken_encoding() -> Any:
    """取 cl100k_base 编码器（惰性加载，失败返回 None 走字符估算兜底）。"""
    global _TIKTOKEN_ENC, _TIKTOKEN_TRIED
    if _TIKTOKEN_TRIED:
        return _TIKTOKEN_ENC
    _TIKTOKEN_TRIED = True
    try:
        import tiktoken
        _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _TIKTOKEN_ENC = None
    return _TIKTOKEN_ENC


@dataclass
class CompressionConfig:
    """压缩配置。"""

    enabled: bool = True
    threshold_percent: float = 0.75
    protect_first_n: int = 2
    protect_last_n: int = 10
    summary_max_chars: int = 2000
    min_compressible: int = 8
    # 尾部保留完整原文的最新工具结果条数（更早的折叠为单行摘要）
    tool_result_fold_keep: int = 4
    # 压缩时保留中间段 user 消息原文（用户承诺/偏好不经摘要有损转述）
    keep_user_messages: bool = True
    # 保留的 user 消息单条字符上限（0 = 不截断；防超大粘贴常驻上下文）
    user_max_chars: int = 2000
    # Microcompact：工具链超过此条数时，清理较早的只读工具结果（0=关闭）
    microcompact_chain_threshold: int = 40
    # Microcompact 保留的最新工具结果条数
    microcompact_keep_recent: int = 6
    # 连续压缩失败熔断次数（对齐 Claude Code MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES）
    max_consecutive_failures: int = 3

    @classmethod
    def from_config_manager(cls) -> "CompressionConfig":
        from core.config import get_config_bool, get_config_float, get_config_int
        return cls(
            enabled=get_config_bool("compression_enabled", True),
            threshold_percent=get_config_float("compression_threshold_percent", 0.75),
            protect_first_n=get_config_int("compression_protect_first_n", 2),
            protect_last_n=get_config_int("compression_protect_last_n", 10),
            summary_max_chars=get_config_int("compression_summary_max_chars", 2000),
            tool_result_fold_keep=get_config_int("compression_tool_fold_keep", 4),
            keep_user_messages=get_config_bool("compression_keep_user_messages", True),
            user_max_chars=get_config_int("compression_user_max_chars", 2000),
            microcompact_chain_threshold=get_config_int("compression_microcompact_threshold", 40),
            microcompact_keep_recent=get_config_int("compression_microcompact_keep", 6),
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
{focus_directive}{previous_block}
要求：分点列出，简洁准确，不超过 {max_chars} 字。直接输出摘要内容，不要额外解释。

[待压缩对话]
{conversation}"""

_FOCUS_DIRECTIVE = (
    "\n【压缩焦点】本次压缩由 AI 主动发起，焦点主题：「{focus_topic}」。"
    "与该主题相关的信息必须完整保留（可超出常规详略标准），"
    "与主题无关的例行对话可从简概括。\n"
)

_PREVIOUS_SUMMARY_BLOCK = (
    "\n【前次摘要】以下是更早对话的既有摘要，请将其与本次片段合并为一份完整摘要"
    "（迭代更新：仍然有效的信息保留，过时的状态以新内容为准）：\n{previous_summary}\n"
)

# 压缩反馈消息的内容前缀：识别前次摘要（迭代压缩时提取，避免"摘要的摘要"）
_COMPRESSION_FEEDBACK_PREFIX = "[上下文压缩]"

# 真用户原话判定：经渠道到达的消息 content 以前缀元数据标签开头
# （[time:…][uid:…][name:…] 等，见 messages.everything.get_tag_list）。
# 机器生成的 user 角色消息（proactive 主动联系指令、自主操作提示、
# prefill 修复后被改写为 user 的 assistant 独白等）没有到达标签——
# 它们属于执行块而非用户原话，应随摘要正常压缩。
_USER_ARRIVAL_TAGS = (
    "time", "uid", "name", "nickname", "channel", "group_id", "session_id", "message_id",
)


def _is_genuine_user_message(msg: Dict) -> bool:
    """判定 role=user 消息是否为真用户原话（渠道到达、带元数据标签）。"""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if not isinstance(content, str) or not content:
        return False
    head = content.lstrip()[:200]
    return head.startswith("[") and any(f"[{tag}:" in head for tag in _USER_ARRIVAL_TAGS)


# 公开别名：供外部模块（如 runtime.bootstrap）引用，避免跨模块导入私有函数
is_genuine_user_message = _is_genuine_user_message


class ContextCompressor:
    """上下文压缩器：检测溢出并压缩中间轮次。"""

    def __init__(self, mind: Any, config: Optional[CompressionConfig] = None) -> None:
        self._mind = mind
        self.config = config or CompressionConfig.from_config_manager()
        self.metrics = CompressionMetrics()
        # 手动压缩请求（compress_context 工具设置，think_loop 消费）
        # scope → focus_topic（空串表示无焦点全量压缩）
        self._manual_requests: Dict[str, str] = {}
        # per-scope 压缩锁：同一 scope 的压缩串行，其他 scope 互不阻塞
        self._scope_locks: Dict[str, asyncio.Lock] = {}
        # 连续失败熔断（对齐 Claude Code：连续 3 次失败后停止尝试）
        self._consecutive_failures = 0
        self._broken = False

    # 可 microcompact 清理的只读工具（对齐 Claude Code COMPACTABLE_TOOLS）
    _MICROCOMPACTABLE_TOOLS = frozenset({
        "read_file", "search_files", "list_directory", "file_info",
        "run_shell_command", "web_fetch", "web_search",
        "extract_page_links", "web_request", "recall", "get_conversation",
    })
    _MICROCOMPACT_PLACEHOLDER = "[旧工具结果已清理，需要时请重新调用工具获取]"

    def microcompact(self, tool_chain: List[Dict]) -> int:
        """清理工具链中较早的只读工具结果为占位符（对齐 Claude Code microCompact）。

        在完整压缩之前先做轻量清理：工具链较长时，只读工具的旧结果
        （read_file/shell/web 等）通常已失效，直接替换为占位符，
        避免触发更重 LLM 摘要压缩。返回清理条数。
        """
        threshold = self.config.microcompact_chain_threshold
        if threshold <= 0 or len(tool_chain) < threshold:
            return 0
        # 定位 role=tool 消息，保留最新 keep_recent 条不动
        tool_msg_indexes = [
            i for i, m in enumerate(tool_chain) if m.get("role") == "tool"
        ]
        keep = self.config.microcompact_keep_recent
        clearable = tool_msg_indexes[:-keep] if len(tool_msg_indexes) > keep else []
        # 建立 tool_call_id → 工具名映射（向前找 assistant 的 tool_calls）
        call_names: Dict[str, str] = {}
        for m in tool_chain:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id"):
                        call_names[tc["id"]] = (tc.get("function") or {}).get("name", "")
        cleared = 0
        for i in clearable:
            msg = tool_chain[i]
            name = call_names.get(msg.get("tool_call_id", ""), "")
            if name not in self._MICROCOMPACTABLE_TOOLS:
                continue
            content = msg.get("content")
            if isinstance(content, str) and len(content) > 200 \
                    and content != self._MICROCOMPACT_PLACEHOLDER:
                tool_chain[i] = {**msg, "content": self._MICROCOMPACT_PLACEHOLDER}
                cleared += 1
        if cleared:
            log(f"Microcompact: 清理 {cleared} 条旧只读工具结果", "DEBUG", tag="压缩")
        return cleared

    def _record_compress_result(self, success: bool) -> None:
        """记录压缩成败，连续失败达阈值熔断。"""
        if success:
            self._consecutive_failures = 0
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.config.max_consecutive_failures:
            self._broken = True
            log(f"上下文压缩连续失败 {self._consecutive_failures} 次，已熔断（本会话不再尝试）",
                "WARNING", tag="压缩")

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
        """估算消息列表的 token 数（tiktoken cl100k_base 优先，chars/4 兜底）。

        chars/4 对中文严重低估（中文约 1 字 ≈ 1~1.5 token），
        会导致压缩触发过晚、撞上 provider 溢出走紧急压缩；
        cl100k_base 与主流模型分词接近，估算偏差显著更小。
        每条消息加计固定结构开销（角色/分隔符等）。
        """
        parts: List[str] = []
        total_chars = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts.extend(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
            for tc in msg.get("tool_calls") or []:
                try:
                    parts.append(json.dumps(tc, ensure_ascii=False))
                except (TypeError, ValueError):
                    pass
        total_chars = sum(len(p) for p in parts)
        enc = _get_tiktoken_encoding()
        if enc is not None:
            try:
                return sum(len(enc.encode(p)) for p in parts) + _TOKENS_PER_MESSAGE * len(messages)
            except Exception:
                pass  # 编码异常（如特殊 token 序列）回退字符估算
        return total_chars // _CHARS_PER_TOKEN

    def should_compress(
            self,
            messages: List[Dict],
            *,
            last_prompt_tokens: int = 0,
            scope: str = "",
    ) -> bool:
        """判断是否需要压缩（真实用量优先，估算兜底；支持手动请求）。"""
        if not self.config.enabled or self._broken:
            return False
        if scope and scope in self._manual_requests:
            return True
        threshold = self.threshold_tokens()
        if threshold <= 0:
            return False
        tokens = last_prompt_tokens or self.estimate_tokens(messages)
        return tokens >= threshold

    def request_manual(self, scope: str, focus_topic: str = "") -> None:
        """请求手动压缩（下一轮 LLM 调用前生效），可指定保留焦点主题。"""
        self._manual_requests[scope] = focus_topic
        log(f"手动压缩请求已登记: scope={scope} focus={focus_topic or '无'}", tag="压缩")

    def pop_manual_focus(self, scope: str) -> Optional[str]:
        """消费该 scope 的手动压缩请求，返回焦点主题（无请求返回 None）。"""
        return self._manual_requests.pop(scope, None)

    def scope_lock(self, scope: str) -> asyncio.Lock:
        """取该 scope 的压缩锁（惰性创建）。"""
        lock = self._scope_locks.get(scope)
        if lock is None:
            lock = asyncio.Lock()
            self._scope_locks[scope] = lock
        return lock

    # ------------------------------------------------------------------
    # 压缩执行
    # ------------------------------------------------------------------

    async def compress_messages(
            self,
            base_messages: List[Dict],
            tool_chain: List[Dict],
            *,
            scope: str = "",
            focus_topic: str = "",
            summarizer: Optional[Callable[[str], Any]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """压缩上下文：保头保尾，中间轮次生成摘要。

        Args:
            base_messages: 基础上下文（system 层 + volatile + 对话历史）
            tool_chain: 当前会话的工具调用链
            scope: 对话 scope（用于缓存失效与手动请求消费）
            focus_topic: 手动压缩的焦点主题（该主题相关信息优先保留）
            summarizer: 摘要生成函数（默认用 mind.llm_chat）

        Returns:
            (新的 base_messages, 新的 tool_chain)
        """
        # 手动请求的焦点优先（compress_context 工具登记，本轮消费）
        manual_focus = self.pop_manual_focus(scope) if scope else None
        if manual_focus is not None:
            focus_topic = manual_focus
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

        # 2.5 修复头部孤儿 tool_calls：head 末尾 assistant 的工具结果若落入
        # middle（将被压缩），则消息序列残缺导致 provider 400。
        # 优先把 middle 开头的对应 tool 结果并入 head；对不上则把该 assistant
        # 消息下放到 middle（其调用信息进摘要，不保留残缺调用结构）。
        head, middle = self._repair_head_orphans(head, middle)
        compressed_count = len(middle)

        # 2.6 分离前次压缩摘要：迭代更新而非"摘要的摘要"（参考 hermes _previous_summary）
        previous_summary, middle = self._extract_previous_summary(middle)

        # 2.7 用户原话保护：抽出 middle 中的 user 消息原文保留（参考 Mini-Agent），
        # 仅 assistant/tool/system 执行块进入摘要——用户的承诺/偏好/情感表达
        # 不经摘要模型的有损二次转述
        preserved_users, middle = self._extract_user_messages(middle)

        # 3. 生成摘要（LLM 失败时回退确定性摘要；无新增内容则直接沿用前次摘要；
        # 中间段只剩用户原话时无需摘要，原文保留即无损）
        if not middle and previous_summary:
            summary = previous_summary
        elif not middle:
            summary = ""
        else:
            summary = await self._summarize(
                middle, summarizer,
                focus_topic=focus_topic, previous_summary=previous_summary,
            )

        # 4. 清理尾部孤儿 tool 消息（其 assistant 调用已被压缩），
        # 并折叠旧工具结果为单行摘要（规则预剪枝，不调 LLM）
        tail = self._sanitize_tail(tail)
        tail = self._fold_tail_tool_results(tail)

        # 5. 重组：头部 system + 保首轮 + 压缩反馈 + 保留的用户原话 + 保尾轮
        new_base = head_system + head
        if summary:
            feedback_content = (
                f"{_COMPRESSION_FEEDBACK_PREFIX} 为节省上下文空间，之前 {compressed_count} 条对话已压缩为以下摘要。"
                "其中包含未完成任务与关键信息，请基于摘要继续：\n"
                f"{summary}"
            )
            if preserved_users:
                feedback_content += "\n（该时段内用户的原话未压缩，已完整保留在下方）"
            new_base.append({"role": "system", "content": feedback_content})
        elif preserved_users:
            new_base.append({"role": "system", "content": (
                f"{_COMPRESSION_FEEDBACK_PREFIX} 为节省上下文空间，早期 {compressed_count} 条执行过程已省略，"
                "期间用户的原话完整保留在下方。"
            )})
        new_base += preserved_users
        new_chain = tail

        after_tokens = self.estimate_tokens(new_base + new_chain)
        self.metrics.record(before_tokens, after_tokens)
        log(
            f"上下文压缩完成: {before_tokens} -> {after_tokens} tokens "
            f"(压缩 {len(middle)} 条, 保留用户原话 {len(preserved_users)} 条, "
            f"比率 {self.metrics.last_ratio})",
            tag="压缩",
        )

        # 6. 压缩后使 Prompt 层缓存失效（volatile 层内容已变化）
        try:
            from agent.mind.prompt_layers import prompt_cache_manager
            prompt_cache_manager.invalidate(scope)
        except Exception:
            pass

        return new_base, new_chain

    @staticmethod
    def _repair_head_orphans(
            head: List[Dict],
            middle: List[Dict],
    ) -> Tuple[List[Dict], List[Dict]]:
        """修复头部末尾 assistant 的悬空 tool_calls。

        不变量：压缩后的消息序列中，每个携带 tool_calls 的 assistant 消息
        之后必须紧跟其全部 tool 结果消息，否则 provider 拒绝整个请求。
        """
        head = list(head)
        middle = list(middle)
        while head and head[-1].get("tool_calls"):
            if middle and middle[0].get("role") == "tool":
                # 结果紧跟其后，一并保留进 head
                head.append(middle.pop(0))
                continue
            # 结果不在 middle 开头（已被更早切走）→ 下放到 middle 进摘要
            middle.insert(0, head.pop())
        return head, middle

    async def _summarize(
            self,
            middle: List[Dict],
            summarizer: Optional[Callable[[str], Any]],
            *,
            focus_topic: str = "",
            previous_summary: str = "",
    ) -> str:
        """生成中间轮次摘要（LLM 优先，失败回退确定性拼接）。

        previous_summary 非空时走迭代更新：LLM 将既有摘要与本次片段合并，
        避免多次压缩退化为"摘要的摘要"（参考 hermes _previous_summary）。
        """
        if summarizer is None:
            summarizer = getattr(self._mind, "summarize_text", None)
        conversation_text = self._render_for_summary(middle)
        focus_directive = (
            _FOCUS_DIRECTIVE.format(focus_topic=focus_topic) if focus_topic else ""
        )
        previous_block = (
            _PREVIOUS_SUMMARY_BLOCK.format(previous_summary=previous_summary)
            if previous_summary else ""
        )
        try:
            if summarizer is not None:
                result = summarizer(
                    _SUMMARY_PROMPT.format(
                        max_chars=self.config.summary_max_chars,
                        conversation=conversation_text,
                        focus_directive=focus_directive,
                        previous_block=previous_block,
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
        fallback = self._fallback_summary(middle)
        if previous_summary:
            return f"{previous_summary}\n{fallback}"
        return fallback

    @staticmethod
    def _extract_previous_summary(middle: List[Dict]) -> Tuple[str, List[Dict]]:
        """从中间段分离前次压缩反馈消息，返回 (前次摘要正文, 剩余消息)。"""
        previous_parts: List[str] = []
        fresh: List[Dict] = []
        for msg in middle:
            content = msg.get("content")
            if (
                msg.get("role") == "system"
                and isinstance(content, str)
                and content.startswith(_COMPRESSION_FEEDBACK_PREFIX)
            ):
                # 反馈格式：首行为说明头，第二行起为摘要正文
                previous_parts.append(content.split("\n", 1)[-1])
            else:
                fresh.append(msg)
        return "\n".join(previous_parts), fresh

    def _extract_user_messages(
            self,
            middle: List[Dict],
    ) -> Tuple[List[Dict], List[Dict]]:
        """抽出 middle 中的真用户原话保留，返回 (保留的 user 消息, 剩余待摘要消息)。

        用户原话（承诺/偏好/情感表达）对陪伴场景价值高，而摘要是有损二次
        转述——语气与细节会丢。保留原文让模型始终读到用户确切说过的话
        （参考 Mini-Agent 压缩策略：user 消息全文保留，仅压缩执行块）。

        只保留携带到达元数据标签的真用户消息（_is_genuine_user_message）；
        机器生成的 user 角色消息（proactive 指令/prefill 修复的独白等）
        属于执行块，留在 middle 随摘要正常压缩。
        单条超 user_max_chars 时截断兜底，防超大粘贴常驻上下文；
        非纯文本内容（视觉图片 block 列表等）不抽出，随摘要处理。
        """
        if not self.config.keep_user_messages:
            return [], middle
        preserved: List[Dict] = []
        rest: List[Dict] = []
        for msg in middle:
            if _is_genuine_user_message(msg):
                content = msg["content"]
                limit = self.config.user_max_chars
                if limit > 0 and len(content) > limit:
                    msg = {**msg, "content": (
                        content[:limit] + f"…（原文 {len(content)} 字符，已截断）"
                    )}
                preserved.append(msg)
            else:
                rest.append(msg)
        return preserved, rest

    @staticmethod
    def _render_for_summary(messages: List[Dict]) -> str:
        """将消息渲染为摘要输入文本（工具结果规则折叠 + 重复去重 + 超长截断）。

        规则预剪枝（参考 hermes _prune_old_tool_results，不调 LLM）：
        工具结果正文对摘要价值低，折叠为单行；相同结果只保留首份。
        渲染结果经统一出向边界清洗：对话内容可能包含用户粘贴的密钥、
        孤代理字符，直接喂给摘要模型有泄漏与 400 双重风险。
        """
        # 工具名映射：tool_call_id → 工具名（折叠结果时标注来源）
        name_map: Dict[str, str] = {}
        for msg in messages:
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name_map[tc.get("id", "")] = fn.get("name", "?")

        lines: List[str] = []
        seen_tool_outputs: set = set()
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content")
            if role == "tool":
                lines.append(ContextCompressor._fold_tool_result_line(
                    name_map.get(msg.get("tool_call_id", ""), "tool"),
                    content, seen_tool_outputs,
                ))
                continue
            if isinstance(content, str) and content:
                lines.append(f"[{role}] {content[:500]}")
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                lines.append(f"[tool_call] {fn.get('name', '?')}({str(fn.get('arguments', ''))[:200]})")
        from core.sanitizer import sanitize_for_context
        return sanitize_for_context("\n".join(lines), max_chars=24_000)

    @staticmethod
    def _fold_tool_result_line(name: str, content: Any, seen: set) -> str:
        """工具结果折叠为单行摘要：错误保留原因，成功截断正文，重复结果省略。"""
        text = content if isinstance(content, str) else str(content or "")
        if text in seen:
            return f"[工具结果] {name}: （与上文重复，已省略）"
        seen.add(text)
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if err or parsed.get("success") is False or parsed.get("ok") is False:
                return f"[工具结果] {name}: 执行失败: {str(err or '未知错误')[:150]}"
        head = text[:120].replace("\n", " ")
        suffix = f"…（原文 {len(text)} 字符）" if len(text) > 120 else ""
        return f"[工具结果] {name}: {head}{suffix}"

    def _fold_tail_tool_results(self, tail: List[Dict]) -> List[Dict]:
        """折叠尾部旧工具结果为单行摘要（最新 tool_result_fold_keep 条保留完整原文）。

        尾部消息每轮常驻上下文，旧工具结果的正文细节对后续推理价值低，
        折叠可显著降低常驻 token；最新几条保留原文供 AI 处理当前任务。
        """
        tool_positions = [i for i, m in enumerate(tail) if m.get("role") == "tool"]
        if len(tool_positions) <= self.config.tool_result_fold_keep:
            return tail
        fold_set = set(tool_positions[: len(tool_positions) - self.config.tool_result_fold_keep])
        name_map: Dict[str, str] = {}
        for msg in tail:
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name_map[tc.get("id", "")] = fn.get("name", "?")
        seen: set = set()
        folded: List[Dict] = []
        for i, msg in enumerate(tail):
            if i in fold_set:
                line = self._fold_tool_result_line(
                    name_map.get(msg.get("tool_call_id", ""), "tool"),
                    msg.get("content"), seen,
                )
                folded.append({**msg, "content": line})
            else:
                folded.append(msg)
        return folded

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
    "当感觉对话变长、响应变慢或收到上下文溢出提示时使用。"
    "可通过 focus_topic 指定你最关心的话题，压缩时该话题相关信息将被优先完整保留。",
)
def _compress_context_tool(reason: str = "", focus_topic: str = "") -> str:
    """手动触发上下文压缩。

    Args:
        reason: 触发原因（仅日志记录）
        focus_topic: 可选的焦点话题，压缩摘要将优先完整保留该话题相关信息
    """
    compressor = get_compressor()
    if compressor is None:
        return json.dumps({"error": "压缩器未初始化"}, ensure_ascii=False)
    try:
        from agent.mind.tool_activation import ToolActivationManager
        scope = ToolActivationManager.current_scope()
    except Exception:
        scope = ""
    compressor.request_manual(scope, focus_topic=focus_topic)
    if reason:
        log(f"AI 请求手动压缩: {reason} (focus={focus_topic or '无'})", tag="压缩")
    return json.dumps({
        "ok": True,
        "message": "压缩请求已登记，将在下一轮 LLM 调用前执行。"
                   + (f"焦点话题「{focus_topic}」的信息将被优先保留。" if focus_topic else ""),
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
        "compression_tool_fold_keep": {
            "description": "压缩时尾部保留完整原文的最新工具结果条数（更早的折叠为单行摘要）",
            "default": 4,
        },
        "compression_keep_user_messages": {
            "description": "压缩时保留中间段 user 消息原文（用户承诺/偏好不经摘要有损转述）",
            "default": True,
        },
        "compression_user_max_chars": {
            "description": "压缩时保留的 user 消息单条字符上限（0 = 不截断）",
            "default": 2000,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_COMPRESSION_CONFIGS)
