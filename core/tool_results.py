"""工具结果的 JSON 解析与错误判定，供执行器、追踪与思维守卫共用。

模型可见：错误摘要保留输出证据；敏感结果可在权限放行后返回明文；展示回执只供内部使用。
Token 影响：回执不增加文本；敏感结果增加原文长度，观测与续跑记录使用短占位。
缓存影响：仅工具反馈与动态态势使用，不触碰稳定前缀。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Optional, Self

from core.sanitizer import sanitize_text

# 失败分支提取错误文本的键优先级
_ERROR_TEXT_KEYS = ("error", "message", "stderr", "detail")

# 单条错误摘要的最大长度（日志可读性截断）
_FALLBACK_BRIEF_MAX = 150

_SENSITIVE_RESULT_PLACEHOLDER = json.dumps({
    "notice": "敏感工具结果仅在原任务中临时可见，记录中已隐藏；需要时请重新调用工具。",
}, ensure_ascii=False)


class PresentedToolResult(str):
    """携带一次性展示回执的内部字符串，回执不参与序列化或模型输入。"""

    _on_presented: Callable[[str], None] | None

    def __new__(cls, text: str, on_presented: Callable[[str], None]) -> Self:
        result = super().__new__(cls, text)
        result._on_presented = on_presented
        return result

    def acknowledge(self, visible: str) -> None:
        """以执行宿主实际交付给接收方的文本提交一次展示事实。"""
        callback, self._on_presented = self._on_presented, None
        if callback is not None:
            callback(visible)


def redact_tool_result(output: str, *, sensitive_result: bool = False) -> str:
    """按执行宿主提供的可见性生成结果副本，不查询注册表或依赖工具实现。"""
    if sensitive_result and not extract_error_text(output):
        return _SENSITIVE_RESULT_PLACEHOLDER
    return sanitize_text(str(output))


def redact_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """复制任务快照并隐藏敏感工具结果，保持调用配对及当前任务原文不变。"""
    snapshot: list[dict[str, Any]] = []
    for message in messages:
        copied = dict(message)
        sensitive_result = copied.pop("_sensitive_result", False) is True
        if message.get("role") == "tool" and isinstance(content := message.get("content"), str):
            copied["content"] = redact_tool_result(content, sensitive_result=sensitive_result)
        if calls := message.get("tool_calls"):
            copied["tool_calls"] = [
                {**call, "function": {**call.get("function", {}),
                    "arguments": sanitize_text(call.get("function", {}).get("arguments", "") or "")}}
                for call in calls
            ]
        snapshot.append(copied)
    return snapshot


def parse_tool_result_json(text: str) -> Optional[Any]:
    """宽松解析工具结果 JSON。

    结果经加工管线后可能带威胁扫描前缀（[安全警告] ...\n）或
    守卫警告后缀（\n\n[工具守卫警告: ...]），整体 json.loads 会失败；
    此处定位首个 '{' 起解析首个完整 JSON 值，容忍前后附加文本。
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


def extract_error_text(payload: Any) -> str:
    """从工具结果 payload（dict 或 JSON 字符串）中提取错误文本，无错误返回空串。

    回退链（仅失败分支内）：error/message/stderr/detail → stdout 与退出码
    → notes → 仅退出码。通用提示不能遮盖程序输出的具体诊断。
    避免把"搜索无匹配"这类否定结果渲染成无信息的"未知错误"。
    """
    if isinstance(payload, str):
        payload = parse_tool_result_json(payload)
    if not isinstance(payload, dict):
        return ""
    if payload.get("success") is False or payload.get("ok") is False:
        rc = payload.get("returncode")
        if rc is None:
            rc = payload.get("exit_code")
        for key in _ERROR_TEXT_KEYS:
            value = payload.get(key)
            if value and str(value).strip():
                if key == "stderr" and rc is not None:
                    brief = f"命令退出码 {rc}；stderr: {' '.join(str(value).split())}"
                    return brief[:_FALLBACK_BRIEF_MAX] + ("…" if len(brief) > _FALLBACK_BRIEF_MAX else "")
                return str(value)
        stdout = payload.get("stdout")
        if isinstance(stdout, str) and stdout.strip():
            prefix = f"命令退出码 {rc}；" if rc is not None else ""
            brief = f"{prefix}stdout: {' '.join(stdout.split())}"
            return brief[:_FALLBACK_BRIEF_MAX] + ("…" if len(brief) > _FALLBACK_BRIEF_MAX else "")
        notes = payload.get("notes")
        if isinstance(notes, list) and notes:
            text = "；".join(str(n) for n in notes if n)
            if text:
                return text[:_FALLBACK_BRIEF_MAX] + ("…" if len(text) > _FALLBACK_BRIEF_MAX else "")
        if rc is not None:
            return f"命令退出码 {rc}（无错误输出）"
        return "工具返回失败但未提供错误详情"
    if payload.get("error"):
        return str(payload["error"])
    return ""
