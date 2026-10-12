"""用户 hook 执行器 — 匹配、串行执行与退出码合并（实现见包 docstring）。"""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import json
import math
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List

from core.command import CommandResult
from core.hooks import HookMode, hook_execution
from core.log import log

from .process import run_hook_command

# 支持的事件（封闭集合：新增事件须同步更新 __init__ docstring 与文档）
HOOK_EVENTS = ("tool_pre", "tool_post", "reply_end")

# 事件的运行时集成点（须与实际调用方同步维护；web 面板据此展示「钩子在哪触发」）
HOOK_INTEGRATIONS: List[Dict[str, str]] = [
    {
        "event": "tool_pre",
        "where": "agent/mind/tools/permission.py",
        "note": "审批门之前执行；exit 2 阻塞工具调用（只能在审批规则之上收紧，不能放宽）",
    },
    {
        "event": "tool_post",
        "where": "agent/mind/tools/think_loop.py",
        "note": "工具执行完成后触发（带结果预览，通知型，无法阻塞）",
    },
    {
        "event": "reply_end",
        "where": "agent/mind/tools/reply_finalize.py",
        "note": "一次回复完成后触发，用于审计与通知，不改写已发送内容",
    },
]

# 单 hook 超时上限（秒）——用户配置值被 clamp 到此值
_MAX_TIMEOUT_SEC = 60.0
_DEFAULT_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class HookSpec:
    """一条 hook 声明。"""

    event: str
    matcher: str            # 工具名 glob（fnmatch）；reply_end 恒 "*"
    command: str
    timeout: float = _DEFAULT_TIMEOUT_SEC
    enabled: bool = True    # 停用保留配置但不执行（web 面板开关）
    mode: HookMode = HookMode.SYNC

    def matches_tool(self, tool_name: str) -> bool:
        return fnmatch.fnmatchcase(tool_name or "*", self.matcher or "*")


@dataclass
class HookOutcome:
    """一次事件的 hook 合并结果。

    合并语义（deny 优先）：同步 tool_pre exit 2 → allowed=False
    （reason 取第一个阻塞理由）；否则 allowed=True。executed 为实际运行的
    hook 数（含非阻塞失败）。
    """

    allowed: bool = True
    reason: str = ""
    executed: int = 0
    scheduled: int = 0
    blocked_by: List[str] = field(default_factory=list)
    # 每条实际运行 hook 的执行明细（matcher/exit code/耗时/阻塞与否），
    # 供 web 测试运行与观测展示
    results: List[Dict[str, Any]] = field(default_factory=list)


def parse_hooks_data(raw: Any) -> Dict[str, List[HookSpec]]:
    """校验并解析 hooks.json 内容为事件分组；非法时抛 ValueError（全量校验）。

    hook 声明字段：matcher（工具名 glob，缺省 "*"）、command（必填）、
    timeout（秒，clamp 到 [1, 60]）、enabled（布尔，缺省 true——停用保留
    配置但不执行）。
    """
    if not isinstance(raw, dict):
        raise ValueError("hooks.json 顶层应为对象")
    hooks: Dict[str, List[HookSpec]] = {e: [] for e in HOOK_EVENTS}
    for event, entries in raw.items():
        if event not in HOOK_EVENTS:
            raise ValueError(f"未知 hook 事件: {event}（可用: {', '.join(HOOK_EVENTS)}）")
        if not isinstance(entries, list):
            raise ValueError(f"hooks.json[{event}] 应为数组")
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("command"):
                raise ValueError(f"hooks.json[{event}] 条目缺少 command")
            if not isinstance(entry["command"], str) or not entry["command"].strip():
                raise ValueError(f"hooks.json[{event}] command 应为非空字符串")
            if not isinstance(entry.get("matcher", "*"), str):
                raise ValueError(f"hooks.json[{event}] matcher 应为字符串")
            try:
                timeout = float(entry.get("timeout", _DEFAULT_TIMEOUT_SEC))
            except (TypeError, ValueError) as exc:
                raise ValueError("timeout 应为有限数值") from exc
            if not math.isfinite(timeout):
                raise ValueError("timeout 应为有限数值")
            if not isinstance(entry.get("mode", "sync"), str) or entry.get("mode", "sync") not in {"sync", "async"}:
                raise ValueError("mode 应为 sync 或 async")
            enabled = entry.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError(f"hooks.json[{event}] 条目 enabled 应为布尔")
            hooks[event].append(HookSpec(
                event=event,
                matcher=str(entry.get("matcher", "*")),
                command=str(entry["command"]),
                timeout=max(1.0, min(timeout, _MAX_TIMEOUT_SEC)),
                enabled=enabled,
                mode=HookMode(entry.get("mode", "sync")),
            ))
    return hooks


def hooks_data_from_specs(hooks: Dict[str, List[HookSpec]]) -> Dict[str, Any]:
    """把事件分组导出为 hooks.json 内容（空事件组省略）。"""
    data: Dict[str, Any] = {}
    for event, specs in hooks.items():
        if not specs:
            continue
        rows: List[Dict[str, Any]] = []
        for spec in specs:
            row: Dict[str, Any] = {
                "matcher": spec.matcher,
                "command": spec.command,
                "timeout": spec.timeout,
                "mode": spec.mode.value,
            }
            if not spec.enabled:
                row["enabled"] = False
            rows.append(row)
        data[event] = rows
    return data


class HookRegistry:
    """hook 配置持有者（fail-closed 加载：坏文件保留上次成功集）。"""

    def __init__(self) -> None:
        self._hooks: Dict[str, List[HookSpec]] = {e: [] for e in HOOK_EVENTS}

    def load(self, path: str) -> int:
        """从 JSON 文件加载，返回加载的 hook 总数（失败抛异常，调用方决定保留旧集）。"""
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._hooks = parse_hooks_data(raw)  # 全量校验通过才提交（原子替换）
        return sum(len(v) for v in self._hooks.values())

    def empty(self) -> bool:
        return not any(self._hooks.values())

    def for_event(self, event: str) -> List[HookSpec]:
        return self._hooks.get(event, [])


_registry = HookRegistry()


def get_hook_registry() -> HookRegistry:
    return _registry


# ------------------------------------------------------------------
# 运行统计（内存观测面：计数 + 最近执行环形记录；重启即清零，不落盘）
# ------------------------------------------------------------------

_RECENT_RUNS_MAX = 20
_recent_runs: Deque[Dict[str, Any]] = deque(maxlen=_RECENT_RUNS_MAX)
_event_stats: Dict[str, Dict[str, Any]] = {
    e: {"executed": 0, "blocked": 0, "failed": 0, "last_run_at": None}
    for e in HOOK_EVENTS
}


def get_hook_stats() -> Dict[str, Any]:
    """每事件执行计数 + 最近执行记录（web 面板「是否生效」的观测数据源）。"""
    return {
        "events": {e: dict(s) for e, s in _event_stats.items()},
        "recent": list(_recent_runs),
    }


def reset_hook_stats() -> None:
    """清零统计（测试隔离用）。"""
    _recent_runs.clear()
    for s in _event_stats.values():
        s.update({"executed": 0, "blocked": 0, "failed": 0, "last_run_at": None})


def _record_run(record: Dict[str, Any]) -> None:
    """记录一次 hook 执行到计数器与环形明细。"""
    _recent_runs.append(record)
    stats = _event_stats.setdefault(
        record["event"], {"executed": 0, "blocked": 0, "failed": 0, "last_run_at": None}
    )
    stats["executed"] += 1
    if record["blocked"]:
        stats["blocked"] += 1
    elif not record["ok"]:
        stats["failed"] += 1
    stats["last_run_at"] = record["at"]


def hooks_active(event: str = "") -> bool:
    """快捷判定（集成点的零开销短路）：总开关关闭或无该事件的启用 hook 即 False。"""
    try:
        from core.config import get_config_bool
        if not get_config_bool("hooks_enabled", True):
            return False
    except Exception:
        return False
    if not event:
        return any(
            spec.enabled for e in HOOK_EVENTS for spec in _registry.for_event(e)
        )
    return any(spec.enabled for spec in _registry.for_event(event))


def reload_hooks(path: str) -> int:
    """加载（或热重载）hooks.json；文件不存在视为空配置（清空旧集）。"""
    global _registry
    if not os.path.isfile(path):
        _registry = HookRegistry()
        return 0
    try:
        new_reg = HookRegistry()
        count = new_reg.load(path)
        _registry = new_reg
        log(f"用户 hooks 已加载: {count} 条（{path}）", tag="Hook")
        return count
    except Exception as exc:
        # fail-closed：保留上次成功集（对齐 permission_rules 语义）
        log(f"hooks.json 加载失败（保留上次配置）: {exc}", "WARNING", tag="Hook")
        return -1


async def _run_spec(spec: HookSpec, payload: Dict[str, Any]) -> Dict[str, Any]:
    """执行单条脚本；只有同步工具前置钩子能够拒绝调用。"""
    async with hook_execution(spec.matcher, spec.event, spec.mode, source="script") as run:
        try:
            result = await run_hook_command(
                spec.command, timeout=spec.timeout,
                env={"HOOK_EVENT": spec.event, "HOOK_TOOL": str(payload.get("tool_name", ""))},
                stdin=json.dumps({**payload, "event": spec.event}, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            result = CommandResult(False, "", f"{type(exc).__name__}: {exc}")
        blocked = spec.event == "tool_pre" and spec.mode == HookMode.SYNC and result.returncode == 2
        run.status = "blocked" if blocked else "done" if result.ok else "error"
        run.detail = (result.stderr or result.stdout).strip()[:1200]
        if not result.ok and not run.detail:
            run.detail = f"exit {result.returncode}"
    record = {
        "at": run.started_at, "event": spec.event, "mode": spec.mode.value,
        "matcher": spec.matcher, "command": spec.command[:80], "ok": result.ok,
        "returncode": result.returncode, "duration_ms": run.duration_ms,
        "blocked": blocked, "test": bool(payload.get("test")), "detail": run.detail,
    }
    _record_run(record)
    if not result.ok:
        log(f"hook {run.status} {spec.event}/{spec.matcher}: {run.detail[:200]}", "WARNING", tag="Hook")
    return record


class ScriptHookRuntime:
    """持有有界的后台脚本任务并在关停时回收进程。"""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Dict[str, Any]]] = set()
        self._pool: asyncio.Semaphore | None = None
        self._closing = False

    async def start(self) -> None:
        self._closing = False
        self._pool = asyncio.Semaphore(4)

    def submit(self, spec: HookSpec, payload: Dict[str, Any]) -> asyncio.Task[Dict[str, Any]] | None:
        if self._closing or len(self._tasks) >= 64:
            log(f"异步 hook 未调度（关停或队列已满）: {spec.event}/{spec.matcher}", "WARNING", tag="Hook")
            return None
        if self._pool is None:
            self._pool = asyncio.Semaphore(4)
        pool = self._pool

        async def execute() -> Dict[str, Any]:
            async with pool:
                return await _run_spec(spec, payload)

        task = asyncio.create_task(execute(), name=f"hook.script.{spec.event}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pool = None


script_hook_runtime = ScriptHookRuntime()


async def run_event_hooks(event: str, *, wait_async: bool = False, **payload: Any) -> HookOutcome:
    """同步执行或后台调度匹配脚本；wait_async 用于管理面的完整测试回执。

    payload 以 JSON 写入 stdin，并镜像到环境变量 HOOK_EVENT/HOOK_TOOL
    （便于简单脚本免解析）。调用方负责先经 hooks_active(event) 短路；
    enabled=false 的条目跳过不执行。每次执行计入运行统计。
    """
    outcome = HookOutcome()
    tool_name = str(payload.get("tool_name", ""))
    pending: list[asyncio.Task[Dict[str, Any]]] = []
    for spec in _registry.for_event(event):
        if tool_name and not spec.matches_tool(tool_name):
            continue
        if not spec.enabled:
            continue
        if spec.mode == HookMode.ASYNC:
            task = script_hook_runtime.submit(spec, copy.deepcopy(payload))
            if task is not None:
                outcome.scheduled += 1
                pending.append(task)
            continue
        record = await _run_spec(spec, payload)
        outcome.executed += 1
        outcome.results.append(record)
        if record["blocked"]:
            outcome.allowed = False
            outcome.blocked_by.append(spec.command[:60])
            if not outcome.reason:
                outcome.reason = str(record["detail"])[:300] or "hook 拒绝（exit 2，无理由输出）"
    if wait_async and pending:
        outcome.results.extend(await asyncio.gather(*pending))
        outcome.executed += len(pending)
    return outcome


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_HOOK_CONFIGS = {
    "system/hooks": {
        "hooks_enabled": {
            "description": "启用用户 hook 事件面（config/hooks.json，工具前/后与回复完成"
                           "事件执行用户脚本；exit 2 阻塞工具调用）。空配置零开销",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_HOOK_CONFIGS)
