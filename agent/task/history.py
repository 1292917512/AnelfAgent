"""任务执行历史 — 每任务保留最近 N 次执行记录（时间/时长/状态/触发来源）。

持久化到 <data_dir>/task_history.json（运行数据，非配置，与 heartbeat.md 同域；
不放 config/tasks/ —— 那里的 *.json 会被任务注册表当作定义文件扫描）。

写入方唯一：TaskExecutor.run 的各终态路径（tick 调度与手动触发共用该汇聚点），
记录失败仅记日志，绝不影响任务执行主流程；读取方为心跳调度（"今日已跑"判据）、
Web（services.task）与 AI（list_tasks 概览 / task_history 明细），均为只读消费。

本模块同时是调度去重的事实源：记录在任务终态即原子落盘，先于任何调度侧记账，
进程重启/崩溃/取消都不会丢失"已完成"这一事实。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List

from core.config import get_config_int, register_configs_safe
from core.file_utils import atomic_write_text
from core.log import log
from core.path import ConfigPaths

# 执行状态词汇（写入方为 executor 内部，读取方按词渲染）
STATUS_SUCCESS = "success"
STATUS_NO_OUTPUT = "no_output"
STATUS_ERROR = "error"
_VALID_STATUSES = frozenset({STATUS_SUCCESS, STATUS_NO_OUTPUT, STATUS_ERROR})

# 非失败状态集合：调度的"今日已跑"判据只认这些（error 保留重试语义，at-least-once）
_GOOD_STATUSES = frozenset({STATUS_SUCCESS, STATUS_NO_OUTPUT})

# 触发来源词汇（心跳调度三种模式 + 手动）
VALID_TRIGGERS = frozenset({"heartbeat", "scheduled", "idle", "manual"})

# 产出摘要与错误信息截断上限（单条记录保持轻量）
_PREVIEW_MAX_CHARS = 200
_ERROR_MAX_CHARS = 300

# 进程内锁：读写互斥（执行路径已由引擎锁串行化，此为防御性兜底）
_LOCK = threading.Lock()


def _history_path() -> Path:
    return Path(ConfigPaths.TASK_HISTORY)


def _max_records() -> int:
    return max(1, get_config_int("task_history_max_records", 5))


def _load_all() -> Dict[str, List[Dict[str, Any]]]:
    """读取全部历史（文件缺失/损坏返回空表，损坏不阻断后续覆写自愈）。"""
    path = _history_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"任务执行历史读取失败（按空处理）: {exc}", "WARNING", tag="任务")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(name): [r for r in records if isinstance(r, dict)]
        for name, records in data.items()
        if isinstance(records, list)
    }


def _save_all(data: Dict[str, List[Dict[str, Any]]]) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _normalize_record(
    task_name: str,
    *,
    started_at: float,
    duration_ms: int,
    status: str,
    trigger: str,
    preview: str,
    error: str,
) -> Dict[str, Any]:
    return {
        "task_name": task_name,
        "started_at": round(float(started_at), 3),
        "duration_ms": max(0, int(duration_ms)),
        "status": status if status in _VALID_STATUSES else STATUS_ERROR,
        "trigger": trigger if trigger in VALID_TRIGGERS else "manual",
        "preview": (preview or "").strip()[:_PREVIEW_MAX_CHARS],
        "error": (error or "").strip()[:_ERROR_MAX_CHARS],
    }


def record_execution(
    task_name: str,
    *,
    started_at: float,
    duration_ms: int,
    status: str,
    trigger: str,
    preview: str = "",
    error: str = "",
) -> bool:
    """记录一次任务执行（追加并按上限裁剪，原子落盘）。返回是否成功。"""
    record = _normalize_record(
        task_name, started_at=started_at, duration_ms=duration_ms,
        status=status, trigger=trigger, preview=preview, error=error,
    )
    try:
        with _LOCK:
            data = _load_all()
            records = data.setdefault(task_name, [])
            records.append(record)
            cap = _max_records()
            if len(records) > cap:
                data[task_name] = records[-cap:]
            _save_all(data)
        return True
    except Exception as exc:
        log(f"任务执行历史写入失败 [{task_name}]: {exc}", "WARNING", tag="任务")
        return False


def get_history(task_name: str) -> List[Dict[str, Any]]:
    """读取指定任务的执行记录（新→旧排序；无记录返回空列表）。"""
    with _LOCK:
        records = list(_load_all().get(task_name, []))
    records.reverse()
    return records


def get_last_good_runs() -> Dict[str, float]:
    """各任务最近一次非失败执行（success/no_output）的开始时间戳（秒）。

    心跳调度判定"槽位是否已跑"的唯一事实源：执行历史在任务终态即原子
    落盘，重启/崩溃/取消不丢"已完成"事实；error 记录不计（失败保留重试
    语义）。记录数受上限裁剪，取的是"最近一次非失败"，与其后是否有失败
    重试无关。无记录的任务不在返回中（调用方按 0 处理）。
    """
    with _LOCK:
        data = _load_all()
    runs: Dict[str, float] = {}
    for name, records in data.items():
        for record in reversed(records):
            if record.get("status") in _GOOD_STATUSES:
                started_at = float(record.get("started_at") or 0.0)
                if started_at > 0:
                    runs[name] = started_at
                break
    return runs


def get_summary() -> Dict[str, Dict[str, Any]]:
    """全部任务的最近一次执行概况：{任务名: {started_at/duration_ms/status/trigger}}。"""
    with _LOCK:
        data = _load_all()
    summary: Dict[str, Dict[str, Any]] = {}
    for name, records in data.items():
        if not records:
            continue
        last = records[-1]
        summary[name] = {
            "started_at": last.get("started_at", 0.0),
            "duration_ms": last.get("duration_ms", 0),
            "status": last.get("status", ""),
            "trigger": last.get("trigger", ""),
        }
    return summary


def clear_history(task_name: str) -> bool:
    """清除指定任务的执行历史（任务删除时调用，防同名重建后残留误导）。"""
    try:
        with _LOCK:
            data = _load_all()
            if task_name not in data:
                return False
            del data[task_name]
            _save_all(data)
        return True
    except Exception as exc:
        log(f"任务执行历史清理失败 [{task_name}]: {exc}", "WARNING", tag="任务")
        return False


def format_duration_ms(duration_ms: int) -> str:
    """毫秒时长渲染为人类可读文本（如 1m24s / 830ms）——Web/AI 消费面共用。"""
    seconds = max(0, duration_ms) / 1000
    if seconds < 1:
        return f"{max(0, duration_ms)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_HISTORY_CONFIGS = {
    "task/history": {
        "task_history_max_records": {
            "description": "每个任务保留的最近执行记录条数（Web 任务页与 AI task_history 可见）",
            "default": 5,
            "min": 1,
            "max": 50,
            "advanced": True,
            "unit": "条",
        },
    },
}

register_configs_safe(_HISTORY_CONFIGS)
