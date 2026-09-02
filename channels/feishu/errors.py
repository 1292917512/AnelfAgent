"""飞书 API 错误归因 — 将 lark 响应码映射为 AI 可消费的结构化错误。

所有 send/media 底层调用在 ``resp.success()`` 为 False 时抛 ``FeishuApiError``；
频道方法统一经 ``to_error_json`` 转为结构化错误 JSON
（success/error/cause/hint/retryable），AI 可据此决策：
参数修正、邀请 Bot 入群、开通权限或稍后重试。
"""

from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from core.tool_errors import ErrorCause, error_from_exception, tool_error


class FeishuApiError(RuntimeError):
    """飞书开放平台 API 业务错误（携带平台错误码）。"""

    def __init__(self, action: str, code: int, msg: str) -> None:
        self.action = action
        self.code = code
        self.api_msg = msg
        super().__init__(f"飞书{action}失败: code={code}, msg={msg}")


# 常见错误码 → (归因, hint, 是否可重试)
_CODE_TABLE: dict[int, Tuple[ErrorCause, str, bool]] = {
    99991663: (ErrorCause.CONFIG, "tenant_access_token 无效，请检查 App ID / App Secret 配置", False),
    99991668: (ErrorCause.CONFIG, "access token 校验失败，请检查应用凭证与 token 类型", False),
    99991672: (ErrorCause.PERMISSION, "应用缺少该接口的权限范围（scope），请到飞书开放平台开通权限并发布新版本", False),
    99991400: (ErrorCause.NETWORK, "触发飞书接口频率限制", True),
    230002: (ErrorCause.PERMISSION, "Bot 不在目标会话中，请先将 Bot 邀请进群后再操作", False),
}


def raise_for_fail(resp: Any, action: str) -> None:
    """lark 响应失败时抛 FeishuApiError（成功则直接返回）。"""
    if resp.success():
        return
    code = int(getattr(resp, "code", -1) or -1)
    msg = str(getattr(resp, "msg", "") or "unknown")
    raise FeishuApiError(action, code, msg)


def not_ready_json() -> str:
    """频道未启动/已停止时的统一错误。"""
    raw = tool_error(
        "飞书频道未就绪（未启动或已停止）",
        cause=ErrorCause.STATE, retryable=False,
        hint="先在频道管理中启动飞书频道，并确认 App ID / App Secret 配置正确",
    )
    payload = json.loads(raw)
    payload["success"] = False
    return json.dumps(payload, ensure_ascii=False)


def to_error_json(exc: Exception, action: str) -> str:
    """将异常统一转为结构化错误 JSON（含 success=False，对齐频道返回约定）。"""
    if isinstance(exc, FeishuApiError):
        mapped: Optional[Tuple[ErrorCause, str, bool]] = _CODE_TABLE.get(exc.code)
        cause = mapped[0] if mapped else ErrorCause.INTERNAL
        hint = mapped[1] if mapped else None
        retryable = mapped[2] if mapped else None
        raw = tool_error(
            f"飞书{exc.action}失败: {exc.api_msg} (code={exc.code})",
            cause=cause, hint=hint, retryable=retryable, code=exc.code,
        )
    else:
        raw = error_from_exception(exc, action=f"飞书{action}")
    payload = json.loads(raw)
    payload["success"] = False
    return json.dumps(payload, ensure_ascii=False)
