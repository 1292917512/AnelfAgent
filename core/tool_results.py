"""工具结果的 JSON 解析与错误判定，供执行器、追踪与思维守卫共用。

模型可见：错误摘要保留命令退出码与输出证据，不推断权限或重试安全性。
Token 影响：命令输出摘要有界，减少无信息失败后的重复尝试。
缓存影响：仅工具反馈与动态态势使用，不触碰稳定前缀。
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
    语义解释）→ returncode/exit_code 与 stdout（远端脚本可能只向 stdout 写诊断）。
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
        notes = payload.get("notes")
        if isinstance(notes, list) and notes:
            text = "；".join(str(n) for n in notes if n)
            if text:
                return text[:_FALLBACK_BRIEF_MAX] + ("…" if len(text) > _FALLBACK_BRIEF_MAX else "")
        if rc is not None:
            stdout = payload.get("stdout")
            if isinstance(stdout, str) and stdout.strip():
                brief = f"命令退出码 {rc}；stdout: {' '.join(stdout.split())}"
                return brief[:_FALLBACK_BRIEF_MAX] + ("…" if len(brief) > _FALLBACK_BRIEF_MAX else "")
            return f"命令退出码 {rc}（无错误输出）"
        return "工具返回失败但未提供错误详情"
    if payload.get("error"):
        return str(payload["error"])
    return ""
