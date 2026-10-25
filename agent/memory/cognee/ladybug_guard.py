"""ladybug native 执行监督：cognee 图后端的进程内适配层。

cognee 的 LadybugAdapter 在独立 SDK 工作进程内运行，四层保障由本模块
以幂等补丁统一收口；主服务另由 RPC 和提交快照隔离原生崩溃：

1. **并发门闸**：ladybug/Kuzu 的 pybind connection 非线程安全，
   ``_submit_to_executor_locked`` 提交到线程池的 native 任务（execute + 结果
   消费全程）包上进程级线程锁串行。关键在 asyncio.wait_for 超时场景——取消
   只能中止 Python 协程，native 查询仍在线程池中运行；锁由执行线程持有，
   native 真正跑完才释放，孤儿查询与后续查询不会并发使用同一 connection。
   ``_drop_native_resources`` 取同一把锁，拆除句柄前必然等在途执行结束。
2. **关闭监督**：close 在独立线程保留 SDK 的封闭提交、排空 executor、
   关闭连接顺序；跨循环调用共享同一个关闭结果，取消等待方不取消关闭。
   delete_graph 保留 SDK 先排空在途查询再拆除的顺序，不提前拆句柄。
   关闭失败向宿主传播，不能被 SDK 缓存的尽力关闭语义掩盖。
3. **失控看门狗**：纯线程实现（事件循环冻死也能工作）。持有门闸超
   ``native_watchdog_restart_seconds`` 经晚绑定端口结束候选工作进程——门闸被
   永久持有会阻止全部图操作。端口未施绑或请求被拒
   时降级为 CRITICAL 告警，看门狗自身绝不直接退出进程。
   设计约束：ladybug 的 ``set_query_timeout``/``interrupt`` 中止路径对
   卡死的扫描算子不可靠（中止动作自身会升级为 SIGSEGV），本模块刻意不做
   native 中止。发布前须正常排空；取消批次由外层回收候选进程并恢复快照。
4. **WAL 容错**：以 ``throw_on_wal_replay_failure=False`` 打开图库，
   允许库能报告的回放错误恢复到已提交事务。此参数不能防止原生崩溃，
   损坏 checkpoint 仍需隔离及全量重建。

安装入口 ``install()`` 由 SDKClient 完成依赖契约检查后调用，幂等。
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
import weakref
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Optional

from core.latebind import LateBinding
from core.log import log

from .config import CogneeConfig

#: 工作进程组合根施绑为结束候选进程，不重启主服务。
#: 未施绑或施绑 None 时失控升级降级为仅告警——测试与未接线环境绝无进程动作。
native_restart_port: LateBinding[Optional[Callable[..., dict[str, Any]]]] = LateBinding(
    "memory.cognee.native_restart",
)

#: 看门狗检查节拍（纯线程 sleep 轮询，开销可忽略）
_WATCHDOG_CHECK_SECONDS = 5.0

#: 已安装标记（类属性，幂等判据）
_INSTALLED_ATTR = "_anel_ladybug_guard"

#: WAL 容错标记（Database 子类幂等判据）
_WAL_TOLERANT_ATTR = "_anel_wal_tolerant"

_checkpoints: ContextVar[CheckpointWindow | None] = ContextVar("cognee_checkpoints", default=None)


class CheckpointWindow:
    """合并受快照保护的写阶段检查点；查询仍正常提交，失败交给外层恢复。"""

    def __init__(self) -> None:
        self._graphs: dict[int, Any] = {}

    def request(self, graph: Any) -> None:
        self._graphs[id(graph)] = graph

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _checkpoints.set(self)
        try:
            yield
        finally:
            _checkpoints.reset(token)

    async def flush(self) -> None:
        """排空阶段内全部请求；任一检查点失败不得批准当前候选。"""
        token = _checkpoints.set(None)
        try:
            for graph in self._graphs.values():
                await graph.checkpoint()
            self._graphs.clear()
        finally:
            _checkpoints.reset(token)


def install_checkpoints(adapter_cls: Any) -> None:
    """只合并显式受保护窗口中的检查点；原生自动检查点和窗口外调用不变。"""
    original = adapter_cls.checkpoint
    if getattr(original, "_anelf_checkpoints", False):
        return

    @functools.wraps(original)
    async def checkpoint(graph: Any) -> None:
        window = _checkpoints.get()
        if window is None:
            await original(graph)
        else:
            window.request(graph)

    checkpoint.__dict__["_anelf_checkpoints"] = True
    adapter_cls.checkpoint = checkpoint


class _GateHold:
    """门闸当前持有记录（锁独占，持有者天然单槽）。"""

    __slots__ = ("kind", "started_ns", "restart_fired")

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.started_ns = time.monotonic_ns()
        self.restart_fired = False


class _GuardState:
    """单 adapter 类的门闸、持有台账与配置来源。"""

    def __init__(self, get_config: Callable[[], CogneeConfig]) -> None:
        self.get_config = get_config
        self.gate = threading.Lock()
        self.hold_lock = threading.Lock()
        self.hold: Optional[_GateHold] = None
        self.adapters: weakref.WeakSet[Any] = weakref.WeakSet()
        self.close_results: set[Future[None]] = set()
        self.teardown_thread = threading.local()

    def begin_hold(self, kind: str) -> None:
        with self.hold_lock:
            self.hold = _GateHold(kind)

    def end_hold(self) -> None:
        with self.hold_lock:
            self.hold = None

    def current_hold(self) -> Optional[_GateHold]:
        with self.hold_lock:
            return self.hold


def _on_event_loop_thread() -> bool:
    """当前线程是否正在运行事件循环（同步阻塞该线程即冻结全局）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _wrap_submit(state: _GuardState, original_submit: Callable[..., Any]) -> Callable[..., Any]:
    """提交路径包装：native 执行全程持门闸，持有台账供看门狗消费。"""

    @functools.wraps(original_submit)
    def gated_submit(self: Any, fn: Any, *args: Any) -> Any:
        @functools.wraps(fn)
        def guarded(*call_args: Any, **call_kwargs: Any) -> Any:
            with state.gate:
                state.begin_hold("query")
                try:
                    return fn(*call_args, **call_kwargs)
                finally:
                    state.end_hold()

        return original_submit(self, guarded, *args)

    return gated_submit


def _wrap_drop(state: _GuardState, original_drop: Callable[..., Any]) -> Callable[..., Any]:
    """拆除路径包装：同在途 native 执行互斥；事件循环线程上获取有界。"""

    @functools.wraps(original_drop)
    def gated_drop(self: Any) -> None:
        if _on_event_loop_thread() and not getattr(state.teardown_thread, "active", False):
            # 事件循环线程永不无限等门闸：预算对齐看门狗 restart 阶梯
            # （此刻进程已在重启路径上），超时抛错而非冻结全局
            budget = state.get_config().native_watchdog_restart_seconds
            if not state.gate.acquire(timeout=budget):
                raise RuntimeError(
                    f"ladybug native 门闸被持有超 {budget:.0f}s，事件循环上的同步拆除已中止（查失控看门狗日志）"
                )
            try:
                state.begin_hold("drop")
                original_drop(self)
            finally:
                state.end_hold()
                state.gate.release()
            return
        with state.gate:
            state.begin_hold("drop")
            try:
                original_drop(self)
            finally:
                state.end_hold()

    return gated_drop


def _wrap_init(state: _GuardState, original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def initialize(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        with state.hold_lock:
            state.adapters.add(self)
    return initialize


def _wrap_close(state: _GuardState, original: Callable[..., Any]) -> Callable[..., Any]:
    """在独立线程完成 SDK 关闭顺序；跨事件循环复用同一个可核验的关闭结果。"""
    @functools.wraps(original)
    async def close(self: Any) -> None:
        with state.hold_lock:
            future: Future[None] | None = getattr(self, "_anel_close_future", None)
            if future is None:
                future = Future()
                self._anel_close_future = future
                state.close_results.add(future)

                def finish(result: Future[None]) -> None:
                    try:
                        state.teardown_thread.active = True
                        # SDK 先封闭 executor 并等待已提交查询，再关闭连接/数据库。
                        asyncio.run(original(self))
                    except BaseException as exc:
                        result.set_exception(exc)
                    else:
                        result.set_result(None)
                        with state.hold_lock:
                            state.close_results.discard(result)

                threading.Thread(target=finish, args=(future,), name="ladybug-close", daemon=False).start()
        await asyncio.shield(asyncio.wrap_future(future))
    return close


def _strict_drop(self: Any) -> None:
    """关闭失败保留句柄并向宿主报告，不能将失败记录成成功关闭。"""
    if self.connection is not None:
        self.connection.close()
        self.connection = None
    if self.db is not None:
        self.db.close()
        self.db = None


async def close_all() -> None:
    """直接核验活跃 Ladybug 适配器关闭；不依赖 SDK 缓存的尽力等待语义。"""
    from cognee.infrastructure.databases.graph.ladybug.adapter import LadybugAdapter

    state: _GuardState | None = getattr(LadybugAdapter, "_anel_guard_state", None)
    if state is None:
        raise RuntimeError("Ladybug 关闭监督未安装，无法确认安全关停")
    with state.hold_lock:
        adapters = list(state.adapters)
    results = await asyncio.gather(*(adapter.close() for adapter in adapters), return_exceptions=True)
    with state.hold_lock:
        pending = list(state.close_results)
    results.extend(await asyncio.gather(*(asyncio.shield(asyncio.wrap_future(future))
                                          for future in pending), return_exceptions=True))
    for result in results:
        if isinstance(result, BaseException):
            raise RuntimeError(f"Ladybug 数据库未能安全关闭: {result}") from result


def _escalate_restart(hold: _GateHold, age_seconds: float) -> None:
    """门闸失控时请求安全关停；不绕过宿主的数据库关闭屏障。"""
    log(
        f"ladybug native {hold.kind} 失控（门闸已持有 {age_seconds:.0f}s），继续持有将饿死全部图操作，请求守护重启",
        "CRITICAL",
        tag="记忆",
    )
    if not native_restart_port.bound:
        log("重启端口未施绑，无法自动重启——请手动 restart.sh", "CRITICAL", tag="记忆")
        return
    request = native_restart_port.get()
    if request is None:
        log("重启端口施绑为 None，无法自动重启——请手动 restart.sh", "CRITICAL", tag="记忆")
        return
    try:
        result = request(source="cognee_native_watchdog")
    except Exception as exc:
        log(f"自动重启请求异常（{exc}）——请手动 restart.sh", "CRITICAL", tag="记忆")
        return
    if not result.get("ok"):
        log(
            f"自动重启被拒（{result.get('error', '未知原因')}）——请手动 restart.sh",
            "CRITICAL",
            tag="记忆",
        )


def _watchdog_check(state: _GuardState) -> None:
    hold = state.current_hold()
    if hold is None:
        return
    config = state.get_config()
    if not config.native_watchdog_enabled:
        return
    age_seconds = (time.monotonic_ns() - hold.started_ns) / 1e9
    if age_seconds > config.native_watchdog_restart_seconds and not hold.restart_fired:
        hold.restart_fired = True
        _escalate_restart(hold, age_seconds)


def _watchdog_loop(state: _GuardState, interval: float) -> None:
    while True:
        time.sleep(interval)
        try:
            _watchdog_check(state)
        except Exception as exc:
            log(f"ladybug 看门狗检查失败（下轮重试）: {exc}", "WARNING", tag="记忆")


def _apply_guard(
    adapter_cls: type,
    get_config: Callable[[], CogneeConfig],
    *,
    watchdog_interval: float = _WATCHDOG_CHECK_SECONDS,
) -> bool:
    """给 LadybugAdapter 类安装门闸/超时/循环安全/看门狗（幂等，返回是否本次安装）。"""
    if getattr(adapter_cls, _INSTALLED_ATTR, False):
        return False
    state = _GuardState(get_config)
    installed: list[str] = []
    wrappers = {
        "__init__": functools.partial(_wrap_init, state),
        "_submit_to_executor_locked": functools.partial(_wrap_submit, state),
        "_drop_native_resources": functools.partial(_wrap_drop, state),
        "close": functools.partial(_wrap_close, state),
    }
    for name, wrap in wrappers.items():
        original = getattr(adapter_cls, name, None)
        if callable(original):
            setattr(adapter_cls, name, wrap(original))
            installed.append(name)
    missing = wrappers.keys() - set(installed)
    if missing:
        log(f"ladybug 监督补丁点缺失（对应防护未装）: {sorted(missing)}", "WARNING", tag="记忆")

    threading.Thread(
        target=_watchdog_loop,
        args=(state, watchdog_interval),
        name="ladybug-native-watchdog",
        daemon=True,
    ).start()
    setattr(adapter_cls, _INSTALLED_ATTR, True)
    # 状态挂类属性：测试直驱 _watchdog_check，运行期可经日志/诊断检查当前持有
    adapter_cls._anel_guard_state = state  # type: ignore[attr-defined]
    return True


def _patch_wal_recovery(ladybug_adapter: Any) -> None:
    """让 cognee 以容错模式打开 ladybug 库（WAL 损坏时恢复而非抛错）。"""
    original = ladybug_adapter.Database
    if getattr(original, _WAL_TOLERANT_ATTR, False):
        return

    class _WalTolerantDatabase(original):  # type: ignore[valid-type, misc]
        """默认容错回放的 Database 包装（保留显式传参覆盖）。"""

        _anel_wal_tolerant = True

        def __init__(self, database_path: Any = None, **kwargs: Any) -> None:
            kwargs.setdefault("throw_on_wal_replay_failure", False)
            super().__init__(database_path, **kwargs)

    ladybug_adapter.Database = _WalTolerantDatabase


def install(get_config: Callable[[], CogneeConfig]) -> bool:
    """安装全部 ladybug 监督补丁（幂等，返回是否本次安装门闸）。"""
    try:
        from cognee.infrastructure.databases.graph.ladybug import adapter as ladybug_adapter
    except Exception as exc:
        log(f"ladybug 监督安装跳过（不影响主流程）: {exc}", "DEBUG", tag="记忆")
        return False
    applied = False
    try:
        if not getattr(ladybug_adapter.LadybugAdapter, _INSTALLED_ATTR, False):
            ladybug_adapter.LadybugAdapter._drop_native_resources = _strict_drop
        applied = _apply_guard(ladybug_adapter.LadybugAdapter, get_config)
    except Exception as exc:
        log(f"ladybug native 监督安装失败（不影响主流程）: {exc}", "WARNING", tag="记忆")
    try:
        _patch_wal_recovery(ladybug_adapter)
    except Exception as exc:
        log(f"ladybug WAL 容错补丁失败（不影响主流程）: {exc}", "WARNING", tag="记忆")
    if applied:
        log("ladybug native 监督已就位（门闸串行 + 循环安全拆除 + 失控看门狗）", "DEBUG", tag="记忆")
    return applied
