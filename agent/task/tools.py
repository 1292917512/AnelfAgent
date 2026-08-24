"""任务定义与心跳调度的 AI 自管理工具。

补齐规划体系的自下而上通道：AI 可以把反复出现的规划沉淀为任务定义
（config/tasks/*.json），并自主绑定/调整心跳调度（heartbeat.json）。
与 Web 管理面（web/routers/config.py）走同一份配置文件与热重载路径，
两侧改动互相可见。工具挂 group="planning"，须在 planning 组激活
之前 import 本模块（deferred 注册按组弹出）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import deferred_tool

_TASK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TASK_MEMORY_TYPES = {"reflection", "semantic", "episodic"}
_SCHEDULE_MODES = {"heartbeat", "scheduled", "idle", "manual"}


def _tasks_dir() -> Path:
    return Path(ConfigPaths.TASKS_DIR)


def _task_path(name: str) -> Path:
    return _tasks_dir() / f"{name}.json"


def _reload_engine() -> None:
    """热重载任务注册表与心跳调度（运行时不可用时静默跳过）。"""
    try:
        from agent.runtime.singleton import require_runtime
        require_runtime().mind.heartbeat_engine.reload()
    except Exception as exc:
        log(f"任务热重载跳过（运行时不可用）: {exc}", "DEBUG", tag="任务")


def _load_task_data(name: str) -> Optional[Dict[str, Any]]:
    path = _task_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_task_data(name: str, data: Dict[str, Any]) -> None:
    from core.file_utils import atomic_write_text
    _tasks_dir().mkdir(parents=True, exist_ok=True)
    atomic_write_text(_task_path(name), json.dumps(data, ensure_ascii=False, indent=2))


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@deferred_tool(
    group="planning", tags=["planning", "heartbeat"], source="mind.task",
    description=(
        "创建一个新的心跳任务定义（沉淀可复用的自治流程）。"
        "当你发现某类工作反复出现、值得定期自动执行时，用它把流程固化下来；"
        "创建后默认 manual 触发，需要自动执行时用 set_task_schedule 绑定调度。"
    ),
)
async def create_task(
    name: str,
    prompt: str,
    display_name: str = "",
    description: str = "",
    memory_type: str = "reflection",
    importance: float = 0.5,
    tags: str = "",
    tool_tags: str = "heartbeat",
    allow_output_tools: bool = False,
    save_result_to_memory: bool = True,
) -> str:
    """创建任务定义。

    Args:
        name: 任务标识（小写字母/数字/下划线，如 daily_summary）
        prompt: 任务执行指令（写给执行时的自己，描述要做什么、怎么做、红线约束）
        display_name: 展示名（默认同 name）
        description: 任务描述（可选）
        memory_type: 结果存入的记忆类型（reflection/semantic/episodic，默认 reflection）
        importance: 结果记忆重要性 0-1（默认 0.5）
        tags: 结果记忆标签，逗号分隔（如 type:reflection,topic:周报）
        tool_tags: 执行时可用的工具标签，逗号分隔（默认 heartbeat）
        allow_output_tools: 是否允许对外发消息（默认 False，仅内部反思类任务）
        save_result_to_memory: 结果是否写入长期记忆（默认 True）
    """
    try:
        if not _TASK_NAME_RE.match(name or ""):
            return tool_error(
                f"任务名非法: {name!r}（须为小写字母/数字/下划线，字母开头）",
                cause=ErrorCause.PARAM, retryable=False,
            )
        if not (prompt or "").strip():
            return tool_error("prompt 不能为空", cause=ErrorCause.PARAM, retryable=False)
        if memory_type not in _TASK_MEMORY_TYPES:
            return tool_error(
                f"memory_type 须为 {sorted(_TASK_MEMORY_TYPES)} 之一",
                cause=ErrorCause.PARAM, retryable=False,
            )
        from .registry import task_files_lock
        async with task_files_lock:
            if _task_path(name).exists():
                return tool_error(
                    f"任务 [{name}] 已存在，修改请用 update_task",
                    cause=ErrorCause.STATE, retryable=False,
                )

            data: Dict[str, Any] = {
                "name": name,
                "display_name": display_name.strip() or name,
                "description": description.strip(),
                "scope": "global",
                "enabled": True,
                "memory_type": memory_type,
                "importance": max(0.0, min(1.0, importance)),
                "tags": _split_csv(tags),
                "source": name,
                "null_keywords": [],
                "tool_tags": _split_csv(tool_tags) or ["heartbeat"],
                "prompt": prompt.strip(),
                "allow_output_tools": allow_output_tools,
                "save_result_to_memory": save_result_to_memory,
            }
            _write_task_data(name, data)
        _reload_engine()
        log(f"🛠 AI 创建任务: {name}", tag="任务")
        return json.dumps({
            "ok": True, "task": name,
            "message": f"任务 [{name}] 已创建（默认 manual 触发）。"
                       "需要自动执行时用 set_task_schedule 绑定心跳/定时调度。",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="创建任务")


@deferred_tool(
    group="planning", tags=["planning", "heartbeat"], source="mind.task",
    description="修改已有任务定义的字段（prompt/启用状态/记忆类型/标签等，空参数不变）。",
)
async def update_task(
    name: str,
    prompt: str = "",
    display_name: str = "",
    description: str = "",
    enabled: str = "",
    memory_type: str = "",
    importance: float = -1.0,
    tags: str = "",
    tool_tags: str = "",
) -> str:
    """修改任务定义。

    Args:
        name: 任务标识
        prompt: 新的执行指令（空串不变）
        display_name: 展示名（空串不变）
        description: 描述（空串不变）
        enabled: "true"/"false"（空串不变）
        memory_type: reflection/semantic/episodic（空串不变）
        importance: 0-1（负数不变）
        tags: 结果记忆标签（空串不变，逗号分隔，整体替换）
        tool_tags: 执行工具标签（空串不变，逗号分隔，整体替换）
    """
    try:
        from .registry import task_files_lock
        async with task_files_lock:
            data = _load_task_data(name)
            if data is None:
                return tool_error(f"任务 [{name}] 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)
            if memory_type and memory_type not in _TASK_MEMORY_TYPES:
                return tool_error(
                    f"memory_type 须为 {sorted(_TASK_MEMORY_TYPES)} 之一",
                    cause=ErrorCause.PARAM, retryable=False,
                )

            changed: List[str] = []
            if prompt.strip():
                data["prompt"] = prompt.strip()
                changed.append("prompt")
            if display_name.strip():
                data["display_name"] = display_name.strip()
                changed.append("display_name")
            if description.strip():
                data["description"] = description.strip()
                changed.append("description")
            if enabled.strip().lower() in ("true", "false"):
                data["enabled"] = enabled.strip().lower() == "true"
                changed.append("enabled")
            if memory_type:
                data["memory_type"] = memory_type
                changed.append("memory_type")
            if importance >= 0:
                data["importance"] = max(0.0, min(1.0, importance))
                changed.append("importance")
            if tags.strip():
                data["tags"] = _split_csv(tags)
                changed.append("tags")
            if tool_tags.strip():
                data["tool_tags"] = _split_csv(tool_tags)
                changed.append("tool_tags")
            if not changed:
                return tool_error("没有任何字段需要更新", cause=ErrorCause.PARAM, retryable=False)

            _write_task_data(name, data)
        _reload_engine()
        log(f"🛠 AI 更新任务: {name} ({', '.join(changed)})", tag="任务")
        return json.dumps({"ok": True, "task": name, "changed": changed}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="更新任务")


@deferred_tool(
    group="planning", tags=["planning", "heartbeat"], source="mind.task",
    description="删除一个任务定义（同时移除其心跳调度绑定）。仅确认任务彻底废弃时使用。",
)
async def delete_task(name: str) -> str:
    """删除任务定义。

    Args:
        name: 任务标识
    """
    try:
        from .registry import task_files_lock
        async with task_files_lock:
            path = _task_path(name)
            if not path.exists():
                return tool_error(f"任务 [{name}] 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)
            path.unlink()

        # 同步移除调度绑定，避免孤儿调度项
        from agent.heartbeat.config import get_heartbeat_config
        cfg = get_heartbeat_config()
        schedule_removed = cfg.remove_schedule(name)
        if schedule_removed:
            cfg.save()
        _reload_engine()
        log(f"🛠 AI 删除任务: {name}（调度绑定移除: {schedule_removed}）", tag="任务")
        return json.dumps({
            "ok": True, "task": name, "schedule_removed": schedule_removed,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="删除任务")


@deferred_tool(
    group="planning", tags=["planning", "heartbeat"], source="mind.task",
    description=(
        "绑定/调整任务的心跳调度：heartbeat=每 N 次心跳执行，scheduled=每天指定时间，"
        "idle=连续 N 次心跳无思考活动后执行（全局仅一条，承载反思+自由活动，"
        "须短于所有 heartbeat 任务的循环），manual=仅手动（移除自动调度）。"
        "创建任务后用它让任务自动跑起来。"
    ),
)
async def set_task_schedule(
    task_name: str,
    mode: str,
    every_n_beats: int = 10,
    schedule_times: str = "",
) -> str:
    """设置任务调度。

    Args:
        task_name: 任务标识
        mode: 调度模式 heartbeat（每 N 次心跳）/ scheduled（每日定时）/
            idle（连续 N 次心跳空闲后执行，全局仅允许一条，任何思考活动都会重置计数）/
            manual（仅手动，移除调度）
        every_n_beats: heartbeat/idle 模式的间隔心跳数（heartbeat 默认 10；
            idle 建议 3-6，且须短于所有 heartbeat 任务的循环拍数）
        schedule_times: scheduled 模式的触发时间，逗号分隔 HH:MM（如 "09:00,21:30"）
    """
    try:
        mode = (mode or "").strip().lower()
        if mode not in _SCHEDULE_MODES:
            return tool_error(
                f"mode 须为 {sorted(_SCHEDULE_MODES)} 之一",
                cause=ErrorCause.PARAM, retryable=False,
            )
        if _load_task_data(task_name) is None:
            return tool_error(
                f"任务 [{task_name}] 不存在，先用 create_task 创建",
                cause=ErrorCause.NOT_FOUND, retryable=False,
            )

        from agent.heartbeat.config import (
            ScheduleMode,
            TaskSchedule,
            get_heartbeat_config,
            validate_schedules,
        )
        cfg = get_heartbeat_config()
        if mode == "manual":
            removed = cfg.remove_schedule(task_name)
            if removed:
                cfg.save()
                _reload_engine()
            return json.dumps({
                "ok": True, "task": task_name, "mode": "manual",
                "message": "已切换为仅手动触发" if removed else "该任务本就无自动调度",
            }, ensure_ascii=False)

        times = _split_csv(schedule_times)
        if mode == "scheduled" and not times:
            return tool_error(
                "scheduled 模式需要提供 schedule_times（如 09:00,21:30）",
                cause=ErrorCause.PARAM, retryable=False,
            )
        schedule = TaskSchedule(
            task_name=task_name,
            mode=ScheduleMode(mode),
            every_n_beats=max(1, every_n_beats),
            schedule_times=times,
        )
        # idle 单例校验：替换视角模拟写入后的调度列表（set_schedule 为 upsert）
        next_schedules = [
            schedule if s.task_name == task_name else s
            for s in cfg.task_schedules
        ]
        if schedule not in next_schedules:
            next_schedules.append(schedule)
        if err := validate_schedules(next_schedules):
            return tool_error(err, cause=ErrorCause.PARAM, retryable=False)
        cfg.set_schedule(schedule)
        cfg.save()
        _reload_engine()
        log(f"🛠 AI 调整任务调度: {task_name} -> {mode}", tag="任务")
        return json.dumps({
            "ok": True, "task": task_name, "mode": mode,
            "every_n_beats": every_n_beats if mode in ("heartbeat", "idle") else None,
            "schedule_times": times if mode == "scheduled" else None,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="设置任务调度")
