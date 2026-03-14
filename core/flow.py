"""
异步流程状态机
支持装饰器注册节点，按顺序执行异步任务
"""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from core.log import log


class FlowState(Enum):
    """节点执行状态"""
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


@dataclass
class NodeResult:
    """节点执行结果"""
    name: str
    state: FlowState
    result: Any = None
    error: Optional[Exception] = None
    duration: float = 0.0


@dataclass
class FlowResult:
    """流程执行结果"""
    success: bool
    results: List[NodeResult] = field(default_factory=list)
    blackboard: Dict[str, Any] = field(default_factory=dict)


class FlowMachine:
    """异步流程状态机"""

    def __init__(self):
        self.blackboard: Dict[str, Any] = {}
        self._nodes: List[tuple[str, Callable, dict]] = []

    def node(self, func=None, *, skip_on_error: bool = False, timeout: Optional[float] = None):
        """节点装饰器，支持 @flow.node 和 @flow.node(参数) 两种用法"""

        def decorator(f: Callable) -> Callable:
            options = {'skip_on_error': skip_on_error, 'timeout': timeout}
            self._nodes.append((f.__name__, f, options))
            log(f"📝 注册节点: {f.__name__}", "DEBUG")
            return f

        return decorator(func) if func else decorator

    async def execute(self) -> FlowResult:
        """异步执行流程"""
        if not self._nodes:
            log("⚠️ 流程中没有节点", "WARNING")
            return FlowResult(success=True, results=[], blackboard=self.blackboard)

        log(f"🚀 开始执行流程，共 {len(self._nodes)} 个节点", "INFO")
        results = []

        for node_name, node_func, options in self._nodes:
            result = await self._execute_node(node_name, node_func, options)
            results.append(result)

            if result.result is not None:
                self.blackboard[f"result_{node_name}"] = result.result

            if result.state == FlowState.FAILURE and not options.get('skip_on_error', False):
                log(f"❌ 流程因节点 {node_name} 失败而终止", "ERROR")
                return FlowResult(success=False, results=results, blackboard=self.blackboard)

        log("🏁 流程执行完成", "INFO")
        return FlowResult(success=True, results=results, blackboard=self.blackboard)

    async def _execute_node(self, name: str, func: Callable, options: dict) -> NodeResult:
        """执行单个节点"""
        start_time = time.time()

        try:
            log(f"▶️ 执行节点: {name}", "INFO")

            timeout = options.get('timeout')
            result = await asyncio.wait_for(func(), timeout=timeout) if timeout else await func()

            duration = time.time() - start_time
            log(f"✅ 节点完成: {name} ({duration:.3f}s)", "INFO")

            return NodeResult(name=name, state=FlowState.SUCCESS, result=result, duration=duration)

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            error = TimeoutError(f"节点 {name} 执行超时 ({options.get('timeout')}s)")
            log(f"⏰ 节点超时: {name}", "ERROR")
            return self._create_error_result(name, error, duration, options.get('skip_on_error', False))

        except Exception as e:
            duration = time.time() - start_time
            log(f"❌ 节点失败: {name} - {e}", "ERROR")

            if options.get('skip_on_error', False):
                log(f"⚠️ 节点 {name} 出错但跳过", "WARNING")

            return self._create_error_result(name, e, duration, options.get('skip_on_error', False))

    def _create_error_result(self, name: str, error: Exception, duration: float, skip: bool) -> NodeResult:
        """创建错误结果"""
        state = FlowState.SKIPPED if skip else FlowState.FAILURE
        return NodeResult(name=name, state=state, error=error, duration=duration)

    def get(self, key: str, default: Any = None) -> Any:
        """获取黑板数据"""
        return self.blackboard.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置黑板数据"""
        self.blackboard[key] = value

    def clear(self) -> None:
        """清空流程和黑板"""
        self._nodes.clear()
        self.blackboard.clear()

    @property
    def node_count(self) -> int:
        """获取节点数量"""
        return len(self._nodes)
