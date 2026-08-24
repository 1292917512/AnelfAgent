"""纯文本回复的自动路由（兜底投递）。

对齐 hermes-agent：对当前用户的最终回复 = 无工具时的 assistant 正文，
由运行时投递一次后结束本轮。

路由规则：纯文本终态无条件投递回来源会话（同一私聊 / 同一群 / 同一子会话）；
其他会话由各自的 REPLY 周期处理，跨会话发送走 switch_session / send_message 工具。
本模块只处理未指定目标的纯文本终态。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from core.log import log
from core.tags import strip_message_meta_tags

# 沉默标记：AI 整条回复恰好是其中之一时视为"决定不回复"（hermes 式精确匹配，
# 正文里提到这些词不会误杀——要求整条规范化后完全相等且有长度上限）
_SILENT_MARKERS = frozenset({"[silent]", "silent", "no_reply", "no reply"})
_SILENT_MAX_LEN = 64


def is_silent(text: str) -> bool:
    """沉默标记精确匹配：整条回复恰好是 [SILENT] 类标记才生效。"""
    if not text:
        return False
    # 规范化空白；剥离边缘标点但保留方括号结构（[SILENT] 与 SILENT 都接受）
    normalized = " ".join(text.split()).strip(" \t.,!?。，！？;；")
    if not normalized or len(normalized) > _SILENT_MAX_LEN:
        return False
    return normalized.lower() in _SILENT_MARKERS


# 沉默旁白（hermes delivery.py 移植 + 中文变体）：整条回复只是一个"沉默姿态"，
# 覆盖 *(silent)*、`silent`、(沉默)、*沉默*、🔇、裸 "." / "…" 等。
# 锚定整条字符串 + 长度上限，正文中包含这些词的正常回复不会被误杀。
_SILENCE_NARRATION_RE = re.compile(
    r"^[\s*_~`]*\(?\s*(silent|silence|no\s+response|no\s+reply)\s*\.?\)?[\s*_~`]*$"
    r"|[\U0001F507.…。]+",
    re.IGNORECASE,
)
# 中文沉默旁白：必须带包裹符号（括号/markdown 标记）才判定，裸词"沉默"可能是正常回答
_SILENCE_NARRATION_CN = frozenset({"沉默", "不回复", "不回应"})
_SILENCE_NARRATION_CN_WRAP = "*_~` \t（）()【】[]"


def is_silence_narration(text: str) -> bool:
    """检测幻觉性的"沉默旁白"文本（整条只是一个姿态标记，无实际内容）。"""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _SILENT_MAX_LEN:
        return False
    if _SILENCE_NARRATION_RE.fullmatch(stripped):
        return True
    inner = stripped.strip(_SILENCE_NARRATION_CN_WRAP)
    return inner != stripped and inner in _SILENCE_NARRATION_CN


def should_suppress(text: str) -> bool:
    """纯文本投递前的抑制判定：显式沉默标记或幻觉沉默旁白 → 不投递。"""
    return is_silent(text) or is_silence_narration(text)


def looks_like_fake_tool_call(text: str) -> bool:
    """检测伪造工具调用/执行记录的文本（不投递给用户，由调用方纠正）。"""
    if not text:
        return False
    return (
        text.startswith("[工具执行记录]")
        or text.startswith("[已执行操作摘要]")
        or text.startswith("call_function")
        or ('"success"' in text[:200] and '"action"' in text[:500])
    )


# 内部注入上下文标记（exec_context / 输出方式提示等，仅供模型阅读）；
# 出现在回复开头说明模型/网关复述了注入内容，禁止投递
_CONTEXT_LEAK_MARKERS = (
    "[系统提示]",
    "[当前场景]",
    "[工具态势]",
    "[当前无外部消息]",
    "[输出方式]",
    "[输出契约]",
)
_CONTEXT_LEAK_SCAN_CHARS = 300


def looks_like_context_leak(text: str) -> bool:
    """检测回复正文是否复述了内部注入的上下文块（不投递，由调用方纠正）。"""
    if not text:
        return False
    head = text[:_CONTEXT_LEAK_SCAN_CHARS]
    return any(marker in head for marker in _CONTEXT_LEAK_MARKERS)


# 前言（过程话术）检测：承诺即将执行动作但无实质结论的过渡文本。
# 这类文本一旦按"纯文本终态"投递就会"只说不做"，由调用方拦截并纠正。
_PREAMBLE_CN_RE = re.compile(
    r"^(?:(?:好的?|OK|嗯+|收到|明白|没问题|稍等(?:一下|片刻)?|等我一下)[，,。！!~～\s]*)?"
    r"(?:让?我|咱)(?:来|先|现在|马上|立即|就|去|这就|帮你|为你|给你)*\s*"
    r"(?:看一?看|查一?查|查一下|看一下|找一?找|找一下|搜索|搜一?搜|试一?试|尝试|"
    r"处理|分析|检查|确认|获取|打开|读取?|运行|执行|验证|研究|了解|调查|整理|统计|翻一?翻)"
)
_PREAMBLE_EN_RE = re.compile(
    r"^(?:(?:sure|okay|ok|alright|got it)[,.!]?\s*)?"
    r"(?:let me|i'll|i will|i'm going to|i am going to|allow me to)\s+"
    r"(?:check|look|search|fetch|find|try|run|read|open|verify|investigate|"
    r"analyze|analyse|take a look|gather|review)",
    re.IGNORECASE,
)
# 前言通常短小；长文本即使以承诺式开头也往往自带结论，不误拦
_PREAMBLE_MAX_LEN = 200


def looks_like_preamble(text: str) -> bool:
    """检测"前言/过程话术"：承诺即将执行动作但没有任何实质结论的过渡文本。"""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _PREAMBLE_MAX_LEN:
        return False
    return bool(_PREAMBLE_CN_RE.match(stripped) or _PREAMBLE_EN_RE.match(stripped))


# 输出工具成功后的短确认文本（"已发送"类）：无信息增量，丢弃防重复出站
_SHORT_ACK_MAX_LEN = 40


def is_short_ack(text: str) -> bool:
    """判断纯文本是否为无信息增量的短确认（输出工具成功后的自言回执）。"""
    if not text:
        return True
    return len(text.strip()) < _SHORT_ACK_MAX_LEN


@dataclass
class ReplyTarget:
    """一个可投递的会话目标。"""

    channel_id: str
    target_id: str
    channel_type: str = "private"  # "private" | "group"
    reply_to: str = ""  # 引用锚点（触发消息的 message_id，可选）
    label: str = ""  # 展示用短标签（如「当前触发」或待办预览）
    session_id: str = ""  # 子会话标识（webui chat_id 等，路由到具体会话窗口）

    @property
    def session_key(self) -> str:
        base = f"{self.channel_id}:{self.channel_type}:{self.target_id}"
        return f"{base}#{self.session_id}" if self.session_id else base


def target_from_anything(anything, adapter_key: str = "") -> Optional[ReplyTarget]:
    """从触发消息解析激活本轮的会话目标（纯文本兜底的默认投递处）。"""
    if anything is None:
        return None
    channel_id = adapter_key or getattr(anything, "adapter_key", "")
    if not channel_id:
        return None
    from agent.messages import EverythingGroup

    if isinstance(anything, EverythingGroup) and anything.is_group_scope:
        target_id = str(anything.group_id)
        channel_type = "group"
    else:
        uid = getattr(anything, "uid", None)
        target_id = str(uid) if uid not in (None, "", 0, "0") else ""
        channel_type = "private"
    if not target_id:
        return None
    reply_to = str(getattr(anything, "adapter_message_id", "") or "")
    return ReplyTarget(
        channel_id=channel_id,
        target_id=target_id,
        channel_type=channel_type,
        reply_to=reply_to,
        label="当前触发会话",
        session_id=str(getattr(anything, "session_id", "") or ""),
    )


def target_from_scope(scope: str, adapter_key: str = "") -> Optional[ReplyTarget]:
    """从 entity scope（"user_qq:123" / "group_qq:456" / "user_webui:u#chat"）构造候选目标（无引用锚点）。

    adapter 段优先取 scope 内嵌值，参数仅作兜底（旧格式 scope 无 adapter 段时）。
    """
    from agent.messages import parse_entity_scope

    scope_type, scope_adapter, base_id, session_id = parse_entity_scope(scope)
    adapter_key = scope_adapter or adapter_key
    if not scope_type or not adapter_key:
        return None
    return ReplyTarget(
        channel_id=adapter_key,
        target_id=base_id,
        channel_type="group" if scope_type == "group" else "private",
        session_id=session_id,
    )


async def deliver_text(target: ReplyTarget, content: str) -> bool:
    """把纯文本回复投递到目标会话，成功返回 True。

    复用 output_tools 的发送管道（频道校验 → 目标解析 → 发送 → 结果解析），
    成功后以 assistant 角色写入对话历史（与 send_message 工具一致）。
    """
    from agent.channel.output_tools import _record_sent_reply, execute_send_action

    # 剥离 LLM 可能模仿历史格式带入的元数据标签（[message_id:xxx] 等）
    content = strip_message_meta_tags(content or "").strip()
    if not content:
        return False

    resolved: dict = {}

    async def _invoke(ch, resolved_target_id: str, channel_type: str):
        resolved["target_id"] = resolved_target_id
        resolved["channel_type"] = channel_type
        kwargs: dict = {"channel_type": channel_type}
        if target.reply_to:
            kwargs["reply_to"] = target.reply_to
        if target.session_id:
            kwargs["session_id"] = target.session_id
        return await ch.send_text(resolved_target_id, content, **kwargs)

    try:
        result = await execute_send_action(
            channel_id=target.channel_id,
            target_id=target.target_id,
            operation="消息",
            invoke=_invoke,
            success_suffix=f" ({len(content)}字, 纯文本投递)",
        )
    except Exception as exc:
        log(f"纯文本投递异常: {exc}", "WARNING", tag="通道")
        return False

    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return False
    if parsed.get("success") is False:
        log(
            f"纯文本投递失败: [{target.channel_id}] -> {target.target_id}: "
            f"{parsed.get('error', '?')}",
            "WARNING", tag="通道",
        )
        return False

    await _record_sent_reply(
        resolved.get("target_id", target.target_id),
        content,
        resolved.get("channel_type", target.channel_type),
        session_id=target.session_id,
        adapter_key=target.channel_id,
    )
    return True
