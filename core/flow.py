"""异步流程状态机 — 依赖拓扑编排 + 声明式重试/超时。

节点经 ``@flow.node`` 注册，execute 前按 graphlib 拓扑分层、同层并发执行：

- ``depends_on`` 显式声明强依赖：上游未 SUCCESS 时本节点标记 UPSTREAM_FAILED 跳过
- 未声明 depends_on 的节点弱链式依赖前驱：保持注册顺序执行、前驱失败不阻断
- skip_on_error 只吞 FAILED（业务异常）；CRASHED（BaseException）记录后穿透
- retries / retry_delay 声明式重试（引擎侧查表退避，超出重复末值）
"""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from graphlib import CycleError, TopologicalSorter
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Union

from core.log import log


class NodeState(Enum):
    """节点执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failure"
    SKIPPED = "skipped"                # 失败但被 skip_on_error 容忍
    UPSTREAM_FAILED = "upstream_failed"  # 强依赖上游未成功，未执行
    CRASHED = "crashed"                # BaseException（取消/进程级事件），穿透


TERMINAL_STATES = frozenset({
    NodeState.SUCCESS, NodeState.FAILED, NodeState.SKIPPED,
    NodeState.UPSTREAM_FAILED, NodeState.CRASHED,
})

NodeFn = Callable[[], Awaitable[Any]]
RetryDelay = Union[float, Sequence[float]]


@dataclass
class NodeResult:
    """节点执行结果。"""

    name: str
    state: NodeState
    result: Any = None
    error: Optional[BaseException] = None
    duration: float = 0.0
    attempts: int = 1


@dataclass
class FlowResult:
    """流程执行结果。"""

    success: bool
    results: List[NodeResult] = field(default_factory=list)
    blackboard: Dict[str, Any] = field(default_factory=dict)

    def counts_message(self) -> str:
        """按状态聚合的摘要（如 "8 success, 1 skipped"）。"""
        counts: Dict[NodeState, int] = {}
        for r in self.results:
            counts[r.state] = counts.get(r.state, 0) + 1
        return ", ".join(f"{n} {s.value}" for s, n in counts.items())

    def by_state(self, state: NodeState) -> List[NodeResult]:
        """按状态筛选节点结果。"""
        return [r for r in self.results if r.state is state]


def result_key(node_name: str) -> str:
    """节点结果在 blackboard 中的键名（execute 写入 ``result_<node_name>``）。

    集中键名生成，避免调用方硬编码 ``result_`` 前缀造成耦合。
    """
    return f"result_{node_name}"


class FlowCycleError(Exception):
    """节点依赖图存在环、重复节点名或引用了未注册节点。"""


@dataclass
class _Node:
    """已注册的节点定义。"""

    name: str
    func: NodeFn
    skip_on_error: bool
    timeout: Optional[float]
    retries: int
    retry_delay: RetryDelay
    depends_on: Optional[List[str]]  # None = 弱链式依赖前驱


class FlowMachine:
    """异步流程状态机（拓扑分层 + 同层并发）。"""

    def __init__(self) -> None:
        self.blackboard: Dict[str, Any] = {}
        self._nodes: List[_Node] = []

    def node(
        self,
        func: Optional[NodeFn] = None,
        *,
        skip_on_error: bool = False,
        timeout: Optional[float] = None,
        retries: int = 0,
        retry_delay: RetryDelay = 0.0,
        depends_on: Optional[Sequence[str]] = None,
    ) -> Callable[..., Any]:
        """节点装饰器，支持 @flow.node 和 @flow.node(参数) 两种用法。"""

        def decorator(f: NodeFn) -> NodeFn:
            self._nodes.append(_Node(
                name=f.__name__, func=f,
                skip_on_error=skip_on_error, timeout=timeout,
                retries=retries, retry_delay=retry_delay,
                depends_on=list(depends_on) if depends_on is not None else None,
            ))
            log(f"📝 注册节点: {f.__name__}", "DEBUG")
            return f

        return decorator(func) if func else decorator

    async def execute(self) -> FlowResult:
        """按拓扑层执行流程：同层并发，硬失败（FAILED）终止，CRASHED 穿透。"""
        if not self._nodes:
            log("⚠️ 流程中没有节点", "WARNING")
            return FlowResult(success=True, results=[], blackboard=self.blackboard)

        layers = self._resolve_layers()
        log(f"🚀 开始执行流程，共 {len(self._nodes)} 个节点 / {len(layers)} 层", "INFO")
        results: List[NodeResult] = []
        states: Dict[str, NodeState] = {}

        for layer in layers:
            runnable: List[_Node] = []
            for node in layer:
                if self._upstream_blocked(node, states):
                    r = NodeResult(name=node.name, state=NodeState.UPSTREAM_FAILED)
                    results.append(r)
                    states[node.name] = r.state
                    log(f"⏭️ 节点跳过（上游未成功）: {node.name}", "WARNING")
                else:
                    runnable.append(node)

            layer_results = list(await asyncio.gather(
                *(self._run_node(n) for n in runnable),
            ))
            for r in layer_results:
                results.append(r)
                states[r.name] = r.state
                if r.result is not None:
                    self.blackboard[result_key(r.name)] = r.result

            crashed = next((r for r in layer_results if r.state is NodeState.CRASHED), None)
            if crashed is not None and crashed.error is not None:
                raise crashed.error
            if any(r.state is NodeState.FAILED for r in layer_results):
                flow_result = FlowResult(success=False, results=results, blackboard=self.blackboard)
                log(f"❌ 流程因节点失败而终止: {flow_result.counts_message()}", "ERROR")
                return flow_result

        flow_result = FlowResult(success=True, results=results, blackboard=self.blackboard)
        log(f"🏁 流程执行完成: {flow_result.counts_message()}", "INFO")
        return flow_result

    def _resolve_layers(self) -> List[List[_Node]]:
        """拓扑分层：未声明依赖的节点弱链前驱；重名/未知依赖/环 → FlowCycleError。"""
        names = [n.name for n in self._nodes]
        if len(set(names)) != len(names):
            raise FlowCycleError(f"节点名重复: {sorted({x for x in names if names.count(x) > 1})}")

        graph: Dict[str, set] = {}
        prev: Optional[str] = None
        known = set(names)
        for n in self._nodes:
            if n.depends_on is not None:
                unknown = set(n.depends_on) - known
                if unknown:
                    raise FlowCycleError(f"节点 {n.name} 依赖未注册节点: {sorted(unknown)}")
                graph[n.name] = set(n.depends_on)
            else:
                graph[n.name] = {prev} if prev else set()
            prev = n.name

        sorter = TopologicalSorter(graph)
        try:
            sorter.prepare()
        except CycleError as exc:
            raise FlowCycleError(f"节点依赖存在环: {exc.args[1]}") from exc

        order = {n.name: i for i, n in enumerate(self._nodes)}
        by_name = {n.name: n for n in self._nodes}
        layers: List[List[_Node]] = []
        while sorter.is_active():
            ready = sorted(sorter.get_ready(), key=order.__getitem__)
            layers.append([by_name[name] for name in ready])
            sorter.done(*ready)
        return layers

    @staticmethod
    def _upstream_blocked(node: _Node, states: Dict[str, NodeState]) -> bool:
        """强依赖上游任一未 SUCCESS 即阻断（弱链式依赖不阻断，保持旧顺序语义）。"""
        if node.depends_on is None:
            return False
        return any(states.get(dep) is not NodeState.SUCCESS for dep in node.depends_on)

    async def _run_node(self, node: _Node) -> NodeResult:
        """执行单个节点（含超时与查表式重试）。"""
        start = time.time()
        for attempt in range(1, node.retries + 2):
            try:
                log(f"▶️ 执行节点: {node.name}", "INFO")
                result = await self._invoke(node)
                duration = time.time() - start
                log(f"✅ 节点完成: {node.name} ({duration:.3f}s)", "INFO")
                return NodeResult(
                    name=node.name, state=NodeState.SUCCESS,
                    result=result, duration=duration, attempts=attempt,
                )
            except Exception as exc:
                if attempt <= node.retries:
                    delay = self._lookup_retry_delay(node, attempt)
                    log(f"🔁 节点重试: {node.name} ({attempt}/{node.retries})，{delay:.1f}s 后", "WARNING")
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                duration = time.time() - start
                if node.skip_on_error:
                    log(f"⚠️ 节点 {node.name} 出错但跳过: {exc}", "WARNING")
                    return NodeResult(
                        name=node.name, state=NodeState.SKIPPED,
                        error=exc, duration=duration, attempts=attempt,
                    )
                log(f"❌ 节点失败: {node.name} - {exc}", "ERROR")
                return NodeResult(
                    name=node.name, state=NodeState.FAILED,
                    error=exc, duration=duration, attempts=attempt,
                )
            except BaseException as exc:
                duration = time.time() - start
                log(f"💥 节点崩溃: {node.name} - {exc}", "ERROR")
                return NodeResult(
                    name=node.name, state=NodeState.CRASHED,
                    error=exc, duration=duration, attempts=attempt,
                )
        raise AssertionError("unreachable")

    async def _invoke(self, node: _Node) -> Any:
        """调用节点函数，可选超时（超时转译为带节点名的 TimeoutError）。"""
        if node.timeout:
            try:
                return await asyncio.wait_for(node.func(), timeout=node.timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(f"节点 {node.name} 执行超时 ({node.timeout}s)") from None
        return await node.func()

    @staticmethod
    def _lookup_retry_delay(node: _Node, attempt: int) -> float:
        """查表取第 attempt 次重试的退避秒数（标量恒定；列表超出重复末值）。"""
        delay = node.retry_delay
        if isinstance(delay, (int, float)):
            return float(delay)
        seq = list(delay)
        if not seq:
            return 0.0
        return float(seq[min(attempt - 1, len(seq) - 1)])

    def get(self, key: str, default: Any = None) -> Any:
        """获取黑板数据。"""
        return self.blackboard.get(key, default)

    def get_result(self, node_name: str, default: Any = None) -> Any:
        """获取某节点的执行结果（blackboard 中 ``result_<node_name>``）。"""
        return self.blackboard.get(result_key(node_name), default)

    def set(self, key: str, value: Any) -> None:
        """设置黑板数据。"""
        self.blackboard[key] = value

    def clear(self) -> None:
        """清空流程和黑板。"""
        self._nodes.clear()
        self.blackboard.clear()

    @property
    def node_count(self) -> int:
        """获取节点数量。"""
        return len(self._nodes)
