"""请求级全量审计日志（参考 Mini-Agent logger：每次请求完整落盘可回放）。

在唯一发送点 Mind._invoke_llm_unified 挂钩：每次 LLM 调用把规整后最终发送的
messages、工具清单、响应（内容/推理/工具调用/usage）、耗时与异常以 JSONL
追加到日轮转文件，供"模型为什么忘了 X / 为什么这样回"类上下文问题回放定位。

隐私说明：审计文件包含完整对话内容，默认关闭（与 log_file_enabled 同款
保守约定），仅本地调试时经配置开启；文件仅写入本地，请自行妥善保管。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from core.log import log

_DEFAULT_AUDIT_DIR = "logs/context_audit"


def is_enabled() -> bool:
    """审计开关（每次调用实时读配置，支持运行时开关）。"""
    try:
        from core.config import get_config_bool
        return get_config_bool("context_audit_enabled", False)
    except Exception:
        return False


def _audit_dir() -> str:
    try:
        from core.config import ConfigManager
        return str(ConfigManager.get("context_audit_dir", _DEFAULT_AUDIT_DIR))
    except Exception:
        return _DEFAULT_AUDIT_DIR


def _log_full_tools() -> bool:
    try:
        from core.config import get_config_bool
        return get_config_bool("context_audit_log_tools", False)
    except Exception:
        return False


def _current_scope() -> str:
    try:
        from agent.mind.tool_activation import ToolActivationManager
        return ToolActivationManager.current_scope() or ""
    except Exception:
        return ""


def _serialize_result(result: Any) -> Optional[Dict[str, Any]]:
    """ChatResult → 可 JSON 序列化字典。"""
    if result is None:
        return None
    usage = getattr(result, "usage", None)
    return {
        "model": getattr(result, "model", "") or "",
        "finish_reason": getattr(result, "finish_reason", "") or "",
        "content": getattr(result, "content", "") or "",
        "reasoning_content": getattr(result, "reasoning_content", "") or "",
        "tool_calls": [
            {
                "id": getattr(tc, "id", ""),
                "name": getattr(tc, "name", ""),
                "arguments": getattr(tc, "arguments", ""),
            }
            for tc in (getattr(result, "tool_calls", None) or [])
        ],
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        } if usage else None,
    }


def _build_record(
        *,
        model: str,
        scope: str,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        result: Any,
        error: Optional[BaseException],
        duration_ms: float,
) -> Dict[str, Any]:
    # 工具 schema 体积大且静态，默认只记名称；调试工具发现问题时再开全量
    if tools and _log_full_tools():
        tools_payload: Any = tools
    else:
        tools_payload = [
            (t.get("function", {}) or {}).get("name", "") for t in (tools or [])
        ]
    record: Dict[str, Any] = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "scope": scope,
        "duration_ms": round(duration_ms, 1),
        "message_count": len(messages),
        "messages": messages,
        "tools": tools_payload,
        "response": _serialize_result(result),
    }
    if error is not None:
        record["error"] = f"{type(error).__name__}: {error}"
    return record


def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


async def record_exchange(
        *,
        model: str,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        result: Any = None,
        error: Optional[BaseException] = None,
        duration_ms: float = 0.0,
        scope: str = "",
) -> None:
    """记录一次 LLM 请求/响应交换（未开启时零开销直接返回）。

    写文件失败静默降级为调试日志——审计是观测手段，绝不影响主流程。
    """
    if not is_enabled():
        return
    try:
        record = _build_record(
            model=model,
            scope=scope or _current_scope(),
            messages=messages,
            tools=tools,
            result=result,
            error=error,
            duration_ms=duration_ms,
        )
        path = os.path.join(
            _audit_dir(), f"audit_{time.strftime('%Y%m%d')}.jsonl",
        )
        await asyncio.to_thread(_append_jsonl, path, record)
    except Exception as exc:
        log(f"上下文审计写入失败: {exc}", "DEBUG", tag="审计")


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_AUDIT_CONFIGS = {
    "上下文审计": {
        "context_audit_enabled": {
            "description": "是否开启请求级全量审计日志（记录完整对话内容，仅供本地调试）",
            "default": False,
        },
        "context_audit_dir": {
            "description": "审计日志目录（按日轮转 JSONL 文件）",
            "default": _DEFAULT_AUDIT_DIR,
        },
        "context_audit_log_tools": {
            "description": "审计日志是否记录完整工具 schema（默认仅工具名，体积更小）",
            "default": False,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_AUDIT_CONFIGS)
