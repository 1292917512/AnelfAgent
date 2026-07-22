"""
异步操作辅助工具
提供统一的异步函数执行和线程池管理功能
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine, Callable, TypeVar
from functools import wraps, partial

from core.log import log

# 定义类型变量
T = TypeVar('T')


class AsyncHelper:
    """异步操作辅助类"""

    @staticmethod
    async def run_in_executor(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """在线程池中执行同步函数
        
        Args:
            func: 要执行的同步函数
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            函数执行结果
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            try:
                # 使用 functools.partial 处理 kwargs 参数
                if kwargs:
                    func_with_kwargs = partial(func, **kwargs)
                    result = await loop.run_in_executor(executor, func_with_kwargs, *args)
                else:
                    result = await loop.run_in_executor(executor, func, *args)
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

        def _thread_worker():
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
    def dual_mode(func: Callable[..., T]) -> Callable[..., T]:
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
        async def async_version(*args: Any, **kwargs: Any) -> T:
            return await AsyncHelper.run_in_executor(func, *args, **kwargs)

        # 将异步版本设置为原函数的属性
        func.async_version = async_version

        return func


# 便捷装饰器
dual_mode = AsyncHelper.dual_mode
