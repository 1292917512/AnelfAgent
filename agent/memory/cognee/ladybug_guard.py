"""ladybug native 执行监督：cognee 图后端的进程内适配层。

cognee 的 LadybugAdapter 按通用嵌入场景设计，在宿主单进程常驻架构下
需要的四层保障由本模块以幂等补丁统一收口：

1. **并发门闸**：ladybug/Kuzu 的 pybind connection 非线程安全，
   ``_submit_to_executor_locked`` 提交到线程池的 native 任务（execute + 结果
   消费全程）包上进程级线程锁串行。关键在 asyncio.wait_for 超时场景——取消
   只能中止 Python 协程，native 查询仍在线程池中运行；锁由执行线程持有，
   native 真正跑完才释放，孤儿查询与后续查询不会并发使用同一 connection。
   ``_drop_native_resources`` 取同一把锁，拆除句柄前必然等在途执行结束。
2. **循环安全拆除**：本地模式的 ``close()``/``delete_graph()`` 在调用线程
   同步拆除资源，发生在事件循环上时门闸等待会冻结整个进程。包装为先把
   拆除经 ``asyncio.to_thread`` 挪到 worker 线程再调原方法（原方法内部的
   同步拆除因句柄已置空自然成为空操作）；同步拆除本体在事件循环线程上
   改为有界获取，超时抛错而非无限冻结。
3. **失控看门狗**：纯线程实现（事件循环冻死也能工作）。持有门闸超
   ``native_watchdog_restart_seconds`` 经晚绑定端口请求守护重启——门闸被
   永久持有会饿死全部图操作，进程重启是唯一出口。端口未施绑或重启被拒
   时降级为 CRITICAL 告警，看门狗自身绝不直接退出进程。
   设计约束：ladybug 的 ``set_query_timeout``/``interrupt`` 中止路径对
   卡死的扫描算子不可靠（中止动作自身会升级为 SIGSEGV），本模块刻意不做
   native 中止——隔离 + 进程级重启是唯一安全语义。
4. **WAL 容错**：以 ``throw_on_wal_replay_failure=False`` 打开图库——
   checkpoint 中途进程被杀时冻结 WAL 尾部的半条记录不再阻塞打开，
   回放到损坏点前最后一个已提交事务；WAL 完好时行为不变。

安装入口 ``install()`` 由 CogneeClient 首次导入 cognee 后调用，幂等；
补丁点缺失时记日志跳过（cognee 升级符号漂移不演变为静默失效）。
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from typing import Any, Callable, Optional

from core.latebind import LateBinding
from core.log import log

from .config import CogneeConfig

#: 看门狗的重启请求通道（组合根施绑 entities.devops.service.request_restart）。
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
        if _on_event_loop_thread():
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


def _wrap_async_teardown(original: Callable[..., Any]) -> Callable[..., Any]:
    """close/delete_graph 包装：拆除先经 to_thread 离循环，再调原方法。

    原方法内部的同步拆除因句柄已置空成为空操作；门闸等待只发生在
    worker 线程，失控执行不再传导为事件循环冻结。
    """

    @functools.wraps(original)
    async def teardown(self: Any, *args: Any, **kwargs: Any) -> Any:
        await asyncio.to_thread(self._drop_native_resources)
        return await original(self, *args, **kwargs)

    return teardown


def _escalate_restart(hold: _GateHold, age_seconds: float) -> None:
    """restart 阶梯：门闸被失控执行永久持有，进程重启是唯一出口。"""
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

    # 补丁点为 cognee 私有实现，类型系统不可见
    original_submit = getattr(adapter_cls, "_submit_to_executor_locked", None)
    if callable(original_submit):
        adapter_cls._submit_to_executor_locked = _wrap_submit(state, original_submit)  # type: ignore[attr-defined]
        installed.append("_submit_to_executor_locked")
    original_drop = getattr(adapter_cls, "_drop_native_resources", None)
    if callable(original_drop):
        adapter_cls._drop_native_resources = _wrap_drop(state, original_drop)  # type: ignore[attr-defined]
        installed.append("_drop_native_resources")
    for name in ("close", "delete_graph"):
        original = getattr(adapter_cls, name, None)
        if callable(original):
            setattr(adapter_cls, name, _wrap_async_teardown(original))
            installed.append(name)
    missing = {"_submit_to_executor_locked", "_drop_native_resources", "close", "delete_graph"} - set(installed)
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
