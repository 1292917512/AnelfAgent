"""心跳调度配置：HeartbeatConfig + TaskSchedule 数据模型。

持久化到 config/heartbeat.json，管理心跳间隔与任务调度绑定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.llm.reasoning import CANONICAL_EFFORTS
from core.log import log
from core.path import ConfigPaths

_REASONING_EFFORT_VALUES = frozenset(CANONICAL_EFFORTS)


def _config_path() -> Path:
    """配置文件路径（每次动态解析 ConfigPaths）。

    刻意不用模块级常量：常量在导入时冻结，与 ConfigPaths 的动态属性
    （可搬迁/可测试隔离）分裂后，load 与 save 会指向不同文件——测试
    隔离 ConfigPaths 时曾因此把内存默认档写回真实 config/heartbeat.json，
    覆盖线上 16 条调度（2026-09-12 第 9-11 次调度脱落事故根因）。
    """
    return Path(ConfigPaths.HEARTBEAT_CONFIG)


def _normalize_reasoning_effort(value: Any) -> str:
    """标准化 reasoning_effort，非法值返回空字符串。"""
    normalized = str(value or "").strip().lower()
    if normalized in _REASONING_EFFORT_VALUES:
        return normalized
    return ""


def _normalize_schedule_times(raw_times: List[Any]) -> List[str]:
    """校验并归一化 SCHEDULED 时间列表为 HH:MM 零填充格式。

    非法值（无法解析为 0-23:0-59）WARNING 跳过，避免字符串比较语义错误。
    """
    result: List[str] = []
    for t in raw_times:
        s = str(t).strip()
        parts = s.split(":")
        if len(parts) != 2:
            log(f"SCHEDULED 时间格式非法（期望 HH:MM）: '{s}'，已跳过", "WARNING", tag="心跳")
            continue
        try:
            h, m = int(parts[0]), int(parts[1])
        except ValueError:
            log(f"SCHEDULED 时间格式非法（期望 HH:MM）: '{s}'，已跳过", "WARNING", tag="心跳")
            continue
        if not (0 <= h <= 23 and 0 <= m <= 59):
            log(f"SCHEDULED 时间超出范围: '{s}'，已跳过", "WARNING", tag="心跳")
            continue
        result.append(f"{h:02d}:{m:02d}")
    return result


class ScheduleMode(str, Enum):
    """任务调度模式。"""

    HEARTBEAT = "heartbeat"
    """每 N 次心跳执行一次。"""

    SCHEDULED = "scheduled"
    """每天指定时间执行。"""

    IDLE = "idle"
    """连续 N 次心跳无思考活动后执行（反思 + 自由活动），全局仅允许一条。

    计数维度是"距上次思考的空闲心跳数"：任何真实思考（回复/反思/任务执行，
    含本任务自身）都会清零计数——因此 idle 任务天然单例串行，只会在 AI 空闲时
    触发，不打断对话节奏。every_n_beats 应短于所有 heartbeat 任务的循环拍数，
    保证空闲窗口优先让给反思与自由活动。
    """

    MANUAL = "manual"
    """仅手动触发（Web / AI 工具）。"""


@dataclass
class TaskSchedule:
    """单个任务在心跳中的调度绑定。

    本类只承载调度定义与节拍计数；"今日是否已跑"不在这里记账——
    执行历史（agent.task.history）在任务终态即原子落盘，是唯一事实源，
    调度重绑/reload 换对象都不会抹掉它。
    """

    task_name: str
    mode: ScheduleMode = ScheduleMode.MANUAL
    every_n_beats: int = 10
    beat_count: int = 0
    schedule_times: List[str] = field(default_factory=list)
    model_id: str = ""
    """指定该调度使用的模型 ID，为空时使用任务定义或默认模型。"""
    reasoning_effort: str = ""
    """调度级思考等级覆盖，为空时使用任务定义或全局设置。"""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "task_name": self.task_name,
            "mode": self.mode.value,
        }
        if self.mode in (ScheduleMode.HEARTBEAT, ScheduleMode.IDLE):
            d["every_n_beats"] = self.every_n_beats
            d["beat_count"] = self.beat_count
        elif self.mode == ScheduleMode.SCHEDULED:
            d["schedule_times"] = self.schedule_times
        if self.model_id:
            d["model_id"] = self.model_id
        effort = _normalize_reasoning_effort(self.reasoning_effort)
        if effort:
            d["reasoning_effort"] = effort
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskSchedule:
        mode = ScheduleMode(data.get("mode", "manual"))
        return cls(
            task_name=data["task_name"],
            mode=mode,
            every_n_beats=int(data.get("every_n_beats", 10)),
            beat_count=int(data.get("beat_count", 0)),
            schedule_times=_normalize_schedule_times(list(data.get("schedule_times", []))),
            model_id=data.get("model_id", ""),
            reasoning_effort=_normalize_reasoning_effort(data.get("reasoning_effort", "")),
        )


@dataclass
class HeartbeatConfig:
    """心跳系统全局配置。"""

    enabled: bool = True
    interval_seconds: int = 300
    analysis_temperature: float = 0.7
    min_conversations_for_analysis: int = 3
    task_schedules: List[TaskSchedule] = field(default_factory=list)

    def get_schedule(self, task_name: str) -> Optional[TaskSchedule]:
        for s in self.task_schedules:
            if s.task_name == task_name:
                return s
        return None

    def set_schedule(self, schedule: TaskSchedule) -> None:
        """添加或更新任务调度绑定。

        重绑（同名替换）时继承既有条目的节拍计数：调度定义的调整（改时间/
        改间隔）不应抹掉运行态进度——计数被清零会让任务提前或延后触发。
        仅在新旧双方都是计数类模式（heartbeat/idle）时继承，其余归零。
        Web 整表保存不走此路径（前端全量回传自带计数值，含显式清零）。
        """
        for i, s in enumerate(self.task_schedules):
            if s.task_name == schedule.task_name:
                counter_modes = (ScheduleMode.HEARTBEAT, ScheduleMode.IDLE)
                if s.mode in counter_modes and schedule.mode in counter_modes:
                    schedule.beat_count = s.beat_count
                self.task_schedules[i] = schedule
                return
        self.task_schedules.append(schedule)

    def remove_schedule(self, task_name: str) -> bool:
        before = len(self.task_schedules)
        self.task_schedules = [s for s in self.task_schedules if s.task_name != task_name]
        return len(self.task_schedules) < before

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "analysis_temperature": self.analysis_temperature,
            "min_conversations_for_analysis": self.min_conversations_for_analysis,
            "task_schedules": [s.to_dict() for s in self.task_schedules],
        }

    def save(self, path: Optional[Path] = None) -> None:
        from core.file_utils import atomic_write_text

        p = path or _config_path()
        content = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        atomic_write_text(p, content)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> HeartbeatConfig:
        p = path or _config_path()
        if not p.exists():
            return _try_migrate()
        try:
            raw = json.loads(p.read_text("utf-8"))
            return _parse_config(raw)
        except Exception as exc:
            log(f"心跳配置加载失败，使用默认值: {exc}", "WARNING", tag="心跳")
            return cls()


def validate_schedules(schedules: List["TaskSchedule"]) -> Optional[str]:
    """写入前校验调度列表，返回错误描述（None = 通过）。

    idle 模式全局仅允许一条：思考会刷新空闲计数（含 idle 任务自身），
    多条 idle 调度会互相抢占计数语义，必须在写入侧拒绝。
    """
    idle_names = [s.task_name for s in schedules if s.mode == ScheduleMode.IDLE]
    if len(idle_names) > 1:
        return f"idle 模式调度仅允许一条（思考会刷新空闲计数，多条会互相抢占），当前有: {idle_names}"
    return None


def _dedupe_idle_schedules(schedules: List[TaskSchedule]) -> List[TaskSchedule]:
    """加载时宽容去重：idle 调度保留首条，多余条目告警丢弃（防手改文件失误卡死加载）。"""
    seen_idle = False
    result: List[TaskSchedule] = []
    for s in schedules:
        if s.mode == ScheduleMode.IDLE:
            if seen_idle:
                log(f"idle 调度仅允许一条，已丢弃重复项: {s.task_name}", "WARNING", tag="心跳")
                continue
            seen_idle = True
        result.append(s)
    return result


def _parse_config(raw: Dict[str, Any]) -> HeartbeatConfig:
    schedules = _dedupe_idle_schedules(
        [TaskSchedule.from_dict(s) for s in raw.get("task_schedules", [])]
    )
    return HeartbeatConfig(
        enabled=raw.get("enabled", True),
        interval_seconds=int(raw.get("interval_seconds", 300)),
        analysis_temperature=float(raw.get("analysis_temperature", 0.7)),
        min_conversations_for_analysis=int(raw.get("min_conversations_for_analysis", 3)),
        task_schedules=schedules,
    )


def _try_migrate() -> HeartbeatConfig:
    """配置文件缺失时构建初始配置（零副作用：不落盘、不动旧文件）。

    首份文件由后续正常写路径（引擎种子化 / tick 计数持久化 / 调度 CRUD）
    创建；此处若主动 save，测试隔离 ConfigPaths 之外的场景（如文件被误删）
    会立即以内存默认档覆盖性重建，掩盖真实故障。
    """
    old_path = Path(ConfigPaths.INTROSPECTION_CONFIG)
    if not old_path.exists():
        return HeartbeatConfig()

    try:
        old = json.loads(old_path.read_text("utf-8"))
        cfg = HeartbeatConfig(
            enabled=old.get("enabled", True),
            analysis_temperature=float(old.get("analysis_temperature", 0.7)),
            min_conversations_for_analysis=int(old.get("min_conversations_for_analysis", 3)),
        )

        old_interval = 300
        try:
            from agent.config import get_config_provider
            old_interval = get_config_provider().mind.heartbeat_interval
        except Exception:
            log("_try_migrate 异常已忽略", "DEBUG")
        cfg.interval_seconds = old_interval

        reflect_hours = float(old.get("reflect_min_hours", 1.0))
        reflect_beats = max(1, int(reflect_hours * 3600 / cfg.interval_seconds))

        introspection_dir = Path(ConfigPaths.INTROSPECTION_DIR)
        if introspection_dir.is_dir():
            for jf in sorted(introspection_dir.glob("*.json")):
                try:
                    data = json.loads(jf.read_text("utf-8"))
                    name = data.get("name", jf.stem)
                    cfg.task_schedules.append(TaskSchedule(
                        task_name=name,
                        mode=ScheduleMode.HEARTBEAT,
                        every_n_beats=reflect_beats,
                    ))
                except Exception:
                    log("_try_migrate 异常已忽略", "DEBUG")

        cfg.task_schedules.append(TaskSchedule(
            task_name="self_reflection",
            mode=ScheduleMode.HEARTBEAT,
            every_n_beats=reflect_beats,
        ))

        return cfg
    except Exception as exc:
        log(f"心跳配置迁移失败: {exc}", "WARNING", tag="心跳")
        return HeartbeatConfig()


_instance: Optional[HeartbeatConfig] = None


def get_heartbeat_config() -> HeartbeatConfig:
    global _instance
    if _instance is None:
        _instance = HeartbeatConfig.load()
    return _instance


def reload_heartbeat_config() -> HeartbeatConfig:
    global _instance
    _instance = HeartbeatConfig.load()
    return _instance
