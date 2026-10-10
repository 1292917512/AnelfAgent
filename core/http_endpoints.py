"""HTTP 扩展端点的认证声明；不依赖 Web 框架。"""
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])
_SELF_AUTH = "__anelf_self_authenticated__"


def self_authenticated(endpoint: F) -> F:
    """声明端点自行验证凭据；处理器必须在访问资源前完成令牌校验。"""
    setattr(endpoint, _SELF_AUTH, True)
    return endpoint


def handles_own_auth(endpoint: object) -> bool:
    """判断已匹配的端点是否声明独立认证。"""
    return getattr(endpoint, _SELF_AUTH, False) is True
