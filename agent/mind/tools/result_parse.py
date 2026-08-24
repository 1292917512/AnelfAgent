"""工具结果解析 — 叶子模块（不依赖 tools/ 内任何兄弟模块）。

工具结果经加工管线后可能带威胁扫描前缀 / 守卫警告后缀等附加文本，
本模块提供宽松 JSON 解析与错误文本提取的纯函数实现，供
think_loop / round_helpers / vision / context_compressor 共同消费。
"""

from __future__ import annotations

import json
from typing import Any, Optional

# 失败分支提取错误文本的键优先级
_ERROR_TEXT_KEYS = ("error", "message", "stderr", "detail")

# 单条错误摘要的最大长度（日志可读性截断）
_FALLBACK_BRIEF_MAX = 150


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

    回退链（仅失败分支内）：error/message/stderr/detail → notes（工具自身的
    语义解释，如 shell"非零码+无输出通常为无匹配"）→ returncode（命令退出码）。
    避免把"搜索无匹配"这类否定结果渲染成无信息的"未知错误"。
    """
    if isinstance(payload, str):
        payload = parse_tool_result_json(payload)
    if not isinstance(payload, dict):
        return ""
    if payload.get("success") is False or payload.get("ok") is False:
        for key in _ERROR_TEXT_KEYS:
            value = payload.get(key)
            if value:
                return str(value)
        notes = payload.get("notes")
        if isinstance(notes, list) and notes:
            text = "；".join(str(n) for n in notes if n)
            if text:
                return text[:_FALLBACK_BRIEF_MAX] + ("…" if len(text) > _FALLBACK_BRIEF_MAX else "")
        rc = payload.get("returncode")
        if rc is not None:
            return f"命令退出码 {rc}（无错误输出）"
        return "工具返回失败但未提供错误详情"
    if payload.get("error"):
        return str(payload["error"])
    return ""
