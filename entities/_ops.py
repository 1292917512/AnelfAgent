"""实体操作态势采集 — 工具执行事实回报框架。

工具经 track_ops 装饰后，每次执行把操作事实（ToolOp）回报给实体的态势
追踪器（filesystem/ops_context、ssh/ops_state），追踪器据此向 volatile 层
注入"本会话正在操作什么"的实时上下文。
"""

from __future__ import annotations

import functools
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

from core.log import log

from ._sdk import get_current_scope

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class ToolOp:
    """一次工具执行的操作事实（track_ops 回报给实体态势追踪器的记录）。

    Attributes:
        scope: 执行所在会话 scope（思维会话外为 "_global"）。
        tool: 工具名（被装饰函数名）。
        target: 展示用目标文本（多目标经 " → " 连接，截断 100 字符）。
        targets: 原始目标参数值（供追踪器提取目录等结构化信息）。
        arguments: 全部绑定参数（供追踪器读取附加参数，如 SSH 连接名）。
        ok: 成败判定（见 track_ops）。
        note: 失败备注（错误消息或退出码，截断 120 字符）。
        duration_ms: 工具执行耗时（毫秒）。
    """

    scope: str
    tool: str
    target: str
    targets: Tuple[str, ...]
    arguments: Dict[str, Any]
    ok: bool
    note: str
    duration_ms: int


def _classify_tool_result(result: Any) -> Tuple[bool, str]:
    """按统一错误契约判定工具结果成败。

    含 error 键 = 失败（备注取错误消息）；含 ok 键取其布尔（失败时
    备注取 returncode/exit_code）；非 JSON 文本（如 read_file 内容）= 成功。
    """
    if not isinstance(result, str):
        return True, ""
    text = result.lstrip()
    if not text.startswith("{"):
        return True, ""
    try:
        data = json.loads(text)
    except ValueError:
        return True, ""
    if not isinstance(data, dict):
        return True, ""
    if data.get("error"):
        return False, str(data["error"])[:120]
    ok = bool(data.get("ok", True))
    note = ""
    if not ok:
        for key in ("returncode", "exit_code"):
            code = data.get(key)
            if code is not None:
                note = f"退出码 {code}"
                break
    return ok, note


def track_ops(
    sink: Callable[[ToolOp], None],
    *target_params: str,
) -> Callable[[F], F]:
    """装饰器：工具执行后把操作事实回报给实体的态势追踪器。

    Args:
        sink: 追踪器入口，接收 ToolOp；异常仅记 DEBUG，绝不影响工具主流程。
        target_params: 构成操作目标的参数名（如 file_path / command），
            其值拼接为展示文本并随 ToolOp.targets 传递原始值。

    成败判定：抛异常 = 失败（原样重抛）；其余按 _classify_tool_result。
    同步/异步工具均适用（包装器保种类，iscoroutinefunction 判定不受影响）。
    """

    def decorator(func: F) -> F:
        tool_name = func.__name__
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            sig = None

        def _report(args: tuple, kwargs: dict, result: Any,
                    exc: Optional[BaseException], started: float) -> None:
            try:
                arguments: Dict[str, Any] = {}
                if sig is not None:
                    arguments = dict(sig.bind_partial(*args, **kwargs).arguments)
                targets = tuple(
                    str(arguments[p]) for p in target_params
                    if arguments.get(p) not in (None, "")
                )
                target = " → ".join(targets)
                if len(target) > 100:
                    target = target[:97] + "..."
                if exc is not None:
                    ok, note = False, f"{type(exc).__name__}: {exc}"[:120]
                else:
                    ok, note = _classify_tool_result(result)
                sink(ToolOp(
                    scope=get_current_scope(), tool=tool_name, target=target,
                    targets=targets, arguments=arguments, ok=ok, note=note,
                    duration_ms=int((time.monotonic() - started) * 1000),
                ))
            except Exception as report_exc:
                log(f"操作态势回报失败（已忽略）: {tool_name} - {report_exc}",
                    "DEBUG", tag="OpsTrack")

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.monotonic()
                try:
                    result = await func(*args, **kwargs)
                except BaseException as exc:
                    _report(args, kwargs, None, exc, started)
                    raise
                _report(args, kwargs, result, None, started)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                _report(args, kwargs, None, exc, started)
                raise
            _report(args, kwargs, result, None, started)
            return result

        return sync_wrapper  # type: ignore[return-value]

    return decorator
