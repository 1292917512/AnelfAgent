"""工具错误返回统一设施 — 面向 AI 的结构化错误反馈。

工具返回给 AI 的错误 JSON 统一由本模块构造：

- ``error`` 为主信号键（框架检测逻辑 round_helpers / guardrails 依赖它识别失败）
- ``cause`` 机器可读归因（参数/配置/权限/网络/超时/状态/内部），AI 据此决策
- ``hint`` 修复指引，仅在能给出可执行建议时提供
- ``retryable`` 告知重试是否有意义，仅在归因明确时给出
- 其余上下文字段（如可选项列表）按需附加，不冗余

``error_from_exception`` 将异常映射为归因明确的错误，避免裸 ``str(exc)``
直接把内部实现细节（路径/SQL/库内部信息）抛给 AI。
"""
from __future__ import annotations

import asyncio
import errno
import json
from enum import Enum
from typing import Any, Dict, Optional

_MAX_DETAIL_LEN = 300


class ErrorCause(str, Enum):
    """错误归因类别。"""

    PARAM = "param"            # 参数错误（修正参数后可重试）
    NOT_FOUND = "not_found"    # 目标不存在
    CONFIG = "config"          # 配置缺失/无效
    PERMISSION = "permission"  # 权限不足
    NETWORK = "network"        # 网络问题
    TIMEOUT = "timeout"        # 超时
    STATE = "state"            # 前置状态不满足（组件未初始化/已禁用等）
    USER_CANCEL = "user_cancel"  # 用户主动取消（禁止自动重试）
    INTERNAL = "internal"      # 内部错误


# 常见网络类 errno（连接拒绝/重置/不可达/域名解析失败等）
_NETWORK_ERRNOS = {
    errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED,
    errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EPIPE,
}


def tool_error(message: str, *, cause: Optional[ErrorCause] = None,
               hint: Optional[str] = None, retryable: Optional[bool] = None,
               **context: Any) -> str:
    """构造统一的工具错误 JSON。

    Args:
        message: 简洁的中文错误描述（一句话说明什么失败了）
        cause: 归因类别，AI 据此判断错误性质
        hint: 修复指引（可执行的下一步建议）
        retryable: 重试是否有意义；归因明确时应给出
        **context: 附加上下文（如 available_channels=[...]），值为 None 时忽略
    """
    payload: Dict[str, Any] = {"error": message}
    if cause is not None:
        payload["cause"] = cause.value
    if hint:
        payload["hint"] = hint
    if retryable is not None:
        payload["retryable"] = retryable
    for key, value in context.items():
        if value is not None:
            payload[key] = value
    return json.dumps(payload, ensure_ascii=False)


def _exc_detail(exc: BaseException) -> str:
    """提取异常细节文本并截断，防止超长输出与内部信息泄露。"""
    detail = str(exc).strip() or type(exc).__name__
    if len(detail) > _MAX_DETAIL_LEN:
        detail = detail[:_MAX_DETAIL_LEN] + "…"
    return detail


def _is_timeout(exc: BaseException) -> bool:
    """按类型与类名识别超时异常（鸭子识别 httpx/requests 等库的超时类型，避免引入依赖）。"""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    return "timeout" in type(exc).__name__.lower()


def _is_network(exc: BaseException) -> bool:
    """识别网络类异常（连接错误、网络类 OSError、常见 HTTP 客户端连接异常）。"""
    if isinstance(exc, ConnectionError):
        return True
    if isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS:
        return True
    name = type(exc).__name__.lower()
    return "connecterror" in name or "network" in name or "dns" in name


def error_from_exception(exc: BaseException, *, action: Optional[str] = None,
                         hint: Optional[str] = None) -> str:
    """将异常转换为归因明确的工具错误 JSON。

    Args:
        exc: 捕获到的异常
        action: 失败的操作描述（如 "读取文件"、"请求 https://..."），
            提供时消息为 "{action}失败: {归因描述}"，否则仅归因描述
        hint: 补充修复指引（覆盖默认 hint 时传入）
    """
    prefix = f"{action}失败: " if action else ""
    detail = _exc_detail(exc)

    if _is_timeout(exc):
        return tool_error(f"{prefix}操作超时 ({detail})",
                          cause=ErrorCause.TIMEOUT, retryable=True, hint=hint)
    if isinstance(exc, PermissionError):
        return tool_error(f"{prefix}权限不足 ({detail})",
                          cause=ErrorCause.PERMISSION, retryable=False,
                          hint=hint or "检查目标路径是否在沙箱允许范围内，或权限配置是否正确")
    if isinstance(exc, FileNotFoundError):
        return tool_error(f"{prefix}目标不存在 ({detail})",
                          cause=ErrorCause.NOT_FOUND, retryable=False,
                          hint=hint or "确认路径/标识是否正确后再试")
    if isinstance(exc, json.JSONDecodeError):
        return tool_error(f"{prefix}数据解析失败 ({detail})",
                          cause=ErrorCause.PARAM, retryable=False,
                          hint=hint or "检查输入是否为合法 JSON")
    if _is_network(exc):
        return tool_error(f"{prefix}网络连接失败 ({detail})",
                          cause=ErrorCause.NETWORK, retryable=True,
                          hint=hint or "检查网络连通性与目标地址后重试")
    if isinstance(exc, (ValueError, KeyError)):
        return tool_error(f"{prefix}参数或数据无效 ({detail})",
                          cause=ErrorCause.PARAM, retryable=False,
                          hint=hint)
    return tool_error(f"{prefix}{type(exc).__name__}: {detail}",
                      cause=ErrorCause.INTERNAL, retryable=False, hint=hint)
