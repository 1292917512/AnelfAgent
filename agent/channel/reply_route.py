"""纯文本回复的自动路由（兜底投递）。

AI 未调用 send_message 而直接输出文字时，由系统把这段文字投递到
激活本轮的会话（来源绑定路由，参考 hermes-agent：路由是系统职责，
AI 无选路权，从结构上杜绝"发错频道"的幻觉）。

send_message 等工具仍是推荐主路径（可 @ 提及、引用回复、跨会话、发媒体），
本模块只处理"未指定频道"的纯文本兜底场景。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional

from core.log import log

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


@dataclass
class ReplyTarget:
    """一个可投递的会话目标。"""

    channel_id: str
    target_id: str
    channel_type: str = "private"  # "private" | "group"
    reply_to: str = ""  # 引用锚点（触发消息的 message_id，可选）

    @property
    def session_key(self) -> str:
        return f"{self.channel_id}:{self.channel_type}:{self.target_id}"

    def describe(self) -> str:
        kind = "群聊" if self.channel_type == "group" else "私聊"
        return f"{self.session_key}（{kind}，频道={self.channel_id}）"


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
    )


def target_from_scope(scope: str, adapter_key: str) -> Optional[ReplyTarget]:
    """从 entity scope（"user_123" / "group_456"）构造候选目标（无引用锚点）。"""
    if not scope or not adapter_key or "_" not in scope:
        return None
    scope_type, scope_id = scope.split("_", 1)
    if not scope_id:
        return None
    return ReplyTarget(
        channel_id=adapter_key,
        target_id=scope_id,
        channel_type="group" if scope_type == "group" else "private",
    )


async def deliver_text(target: ReplyTarget, content: str) -> bool:
    """把纯文本回复投递到目标会话，成功返回 True。

    复用 output_tools 的发送管道（频道校验 → 目标解析 → 发送 → 结果解析），
    成功后以 assistant 角色写入对话历史（与 send_message 工具一致）。
    """
    from agent.channel.output_tools import _execute_send_action, _record_sent_reply

    if not content or not content.strip():
        return False

    resolved: dict = {}

    async def _invoke(ch, resolved_target_id: str, channel_type: str):
        resolved["target_id"] = resolved_target_id
        resolved["channel_type"] = channel_type
        kwargs: dict = {"channel_type": channel_type}
        if target.reply_to:
            kwargs["reply_to"] = target.reply_to
        return await ch.send_text(resolved_target_id, content, **kwargs)

    try:
        result = await _execute_send_action(
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
        message_id=str(parsed.get("message_id") or ""),
    )
    return True


def extract_route_choice(text: str, candidates: List[ReplyTarget]) -> Optional[ReplyTarget]:
    """从 AI 的路由回答中提取目标会话（纯逻辑提取，失败返回 None 由调用方回退）。

    匹配优先级：完整 session_key > target_id > 编号（1-based）。
    """
    if not text or not candidates:
        return None
    normalized = " ".join(text.split())

    for c in candidates:
        if c.session_key in normalized:
            return c
    for c in candidates:
        if c.target_id and c.target_id in normalized:
            return c
    m = re.search(r"(?:^|[^\d])(\d{1,2})(?:[^\d]|$)", normalized)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
    return None
