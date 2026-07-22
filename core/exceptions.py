"""
全局异常捕获装饰器
统一的异常处理
"""
import asyncio
from functools import wraps
from typing import Any, Callable, Optional, Type, Tuple

from core.log import error


def catch_exceptions(*, reraise: bool = True, default_value: Any = None, tag: Optional[str] = None,
                     catch_types: Optional[Tuple[Type[Exception], ...]] = None,
                     callback: Optional[Callable[[Exception, str], Any]] = None) -> Callable:
    """
    统一的异常捕获装饰器
    
    Args:
        reraise: 是否重新抛出异常，默认True
        default_value: 当reraise=False时返回的默认值，默认None
        tag: 日志标签，用于标识模块
        catch_types: 指定要捕获的异常类型，None表示捕获所有异常
        callback: 自定义异常处理回调函数 (exception, func_name) -> Any
    """
    target_exceptions = catch_types or (Exception,)

    def _handle(e: Exception, func_name: str) -> Any:
        """统一异常处理：日志 → 回调 → 重抛/默认值。"""
        error_msg = f"❌ {func_name}() 执行异常: {type(e).__name__}: {str(e)}"
        error(error_msg, tag)

        # 自定义回调处理
        if callback:
            try:
                return callback(e, func_name)
            except Exception as cb_error:
                error(f"❌ 异常回调执行失败: {str(cb_error)}", tag)

        # 根据设置处理异常
        if reraise:
            raise e
        return default_value

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except target_exceptions as e:
                    return _handle(e, func.__name__)
            return async_wrapper

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except target_exceptions as e:
                return _handle(e, func.__name__)
        return wrapper

    return decorator
