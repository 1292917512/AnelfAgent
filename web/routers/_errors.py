"""路由层统一的 500 构造 helper：内部细节进日志，响应只含操作描述。"""

from __future__ import annotations

from fastapi import HTTPException

from core.log import log


def server_error(action: str, exc: Exception) -> HTTPException:
    """构造 500 响应：异常细节写入日志，detail 不泄露内部信息。"""
    log(f"{action}失败: {exc}", "WARNING")
    return HTTPException(status_code=500, detail=f"{action}失败")
