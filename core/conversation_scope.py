"""异步调用链的会话归属，供工具、记忆与规划共享，不依赖思维实现。"""

from contextvars import ContextVar, Token

_scope: ContextVar[str] = ContextVar("conversation_scope", default="")


def bind_scope(scope: str) -> Token[str]:
    """绑定会话并返回复位凭据。"""
    return _scope.set(scope)


def reset_scope(token: Token[str]) -> None:
    """恢复进入当前调用链之前的会话。"""
    _scope.reset(token)


def current_scope() -> str:
    """返回当前会话，未绑定时使用全局作用域。"""
    return _scope.get() or "_global"
