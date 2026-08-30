"""
异步操作辅助工具
提供统一的异步函数执行和线程池管理功能
"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial, wraps
from typing import Any, Callable, Coroutine, ParamSpec, Protocol, TypeVar, cast

from core.log import log

# 定义类型变量
T = TypeVar('T')
P = ParamSpec('P')
R = TypeVar('R')


class DualModeCallable(Protocol[P, R]):
    """@dual_mode 装饰器返回的可调用对象：同步调用签名不变，并挂载 async_version 属性。"""

    async_version: Callable[P, Coroutine[Any, Any, R]]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...

# 共享线程池的工作线程数：I/O 密集型任务按 CPU 核数放大
_EXECUTOR_MAX_WORKERS = min(32, (os.cpu_count() or 4) + 4)

# 模块级共享线程池：避免每次调用新建/销毁线程池的开销
_shared_executor = ThreadPoolExecutor(
    max_workers=_EXECUTOR_MAX_WORKERS,
    thread_name_prefix="async_helper",
)


def shutdown_shared_executor() -> None:
    """关闭共享线程池（进程退出时由 Lifecycle 调用）。"""
    _shared_executor.shutdown(wait=False, cancel_futures=True)


# 强引用集合：事件循环对 task 只持弱引用，fire-and-forget 任务在
# 两次 await 之间可能被 GC 回收而静默夭折；done 回调取回异常并记日志
_background_tasks: "set[asyncio.Task]" = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str = "") -> asyncio.Task:
    """创建受管后台任务：强引用防 GC + 异常取回记日志（替代裸 create_task）。"""
    task = asyncio.create_task(coro, name=name or None)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log(f"后台任务异常 ({name or t.get_name()}): "
                f"{type(exc).__name__}: {exc}", "ERROR")

    _background_tasks.add(task)
    task.add_done_callback(_done)
    return task


def _register_shutdown_hook() -> None:
    """将共享线程池的清理回调注册到 Lifecycle。"""
    try:
        from core.lifecycle import Lifecycle
        Lifecycle.register(
            "async_helper_executor",
            _shared_executor,
            cleanup=shutdown_shared_executor,
        )
    except Exception as e:
        log(f"共享线程池关闭钩子注册失败: {e}", "DEBUG")


_register_shutdown_hook()


class AsyncHelper:
    """异步操作辅助类"""

    @staticmethod
    async def run_in_executor(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """在共享线程池中执行同步函数

        Args:
            func: 要执行的同步函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数执行结果
        """
        loop = asyncio.get_event_loop()
        try:
            # 使用 functools.partial 处理 kwargs 参数
            if kwargs:
                func_with_kwargs = partial(func, **kwargs)
                result = await loop.run_in_executor(_shared_executor, func_with_kwargs, *args)
            else:
                result = await loop.run_in_executor(_shared_executor, func, *args)
            return result
        except Exception as e:
            log(f"❌ 线程池执行函数失败: {func.__name__} - {str(e)}", "ERROR")
            raise

    @staticmethod
    def safe_run_async(coro_func: Callable[..., Any], *args: Any, timeout: float = 30, **kwargs: Any) -> Any:
        """安全执行异步函数，自动处理事件循环冲突
        
        Args:
            coro_func: 函数对象
            *args: 位置参数
            timeout: 超时时间（秒）
            **kwargs: 关键字参数
            
        Returns:
            函数执行结果
        """
        # 创建协程对象或直接获取结果
        try:
            coro = AsyncHelper._create_coroutine(coro_func, *args, **kwargs)
        except RuntimeError as e:
            if "running event loop" in str(e):
                log("⚠️ 检测到运行中的事件循环，切换到新线程执行", "WARNING")
                return AsyncHelper._run_in_new_thread(coro_func, *args, timeout=timeout, **kwargs)
            log(f"❌ 创建协程失败: {str(e)}", "ERROR")
            raise

        # 非协程对象直接返回结果
        if not isinstance(coro, Coroutine):
            return coro

        # 安全运行协程
        try:
            result = asyncio.run(coro)
            return result
        except RuntimeError as e:
            if "running event loop" in str(e):
                log("⚠️ 检测到运行中的事件循环，切换到新线程执行", "WARNING")
                return AsyncHelper._run_in_new_thread(coro_func, *args, timeout=timeout, **kwargs)
            log(f"❌ 异步函数执行失败: {coro_func.__name__} - {str(e)}", "ERROR")
            raise

    @staticmethod
    def _create_coroutine(coro_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """创建协程对象或返回同步结果"""
        if asyncio.iscoroutinefunction(coro_func):
            return coro_func(*args, **kwargs)

        result = coro_func(*args, **kwargs)
        return result if asyncio.iscoroutine(result) else result

    @staticmethod
    def _run_in_new_thread(coro_func: Callable[..., Any], *args: Any, timeout: float = 30, **kwargs: Any) -> Any:
        """在新线程中执行异步函数"""
        worker_loops: list = []

        def _thread_worker() -> Any:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            worker_loops.append(loop)
            try:
                coro = AsyncHelper._create_coroutine(coro_func, *args, **kwargs)
                if isinstance(coro, Coroutine):
                    result = loop.run_until_complete(coro)
                else:
                    result = coro
                return result
            except Exception as e:
                log(f"❌ 新线程中异步函数执行失败: {coro_func.__name__} - {str(e)}", "ERROR")
                raise
            finally:
                loop.close()

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_thread_worker)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            log(f"⏰ 异步函数执行超时: {coro_func.__name__} (超时: {timeout}s)", "ERROR")
            # 停止线程内事件循环，避免 shutdown(wait=True) 使超时形同虚设
            if worker_loops:
                worker_loops[0].call_soon_threadsafe(worker_loops[0].stop)
            raise
        except Exception as e:
            log(f"❌ 线程池执行异步函数失败: {coro_func.__name__} - {str(e)}", "ERROR")
            raise
        finally:
            executor.shutdown(wait=False)

    @staticmethod
    def dual_mode(func: Callable[P, R]) -> DualModeCallable[P, R]:
        """为同步函数自动生成异步版本

        使用方法：
            @dual_mode
            def my_function(arg1, arg2):
                return result

        生成：
            my_function() - 同步版本
            my_function.async_version() - 异步版本
        """

        # 异步版本
        @wraps(func)
        async def async_version(*args: P.args, **kwargs: P.kwargs) -> R:
            return await AsyncHelper.run_in_executor(func, *args, **kwargs)

        # 将异步版本设置为原函数的属性
        wrapped = cast(DualModeCallable[P, R], func)
        wrapped.async_version = async_version

        return wrapped


# 便捷装饰器
dual_mode = AsyncHelper.dual_mode
