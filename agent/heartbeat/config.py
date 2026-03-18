"""心跳调度配置：HeartbeatConfig + TaskSchedule 数据模型。

持久化到 config/heartbeat.json，管理心跳间隔与任务调度绑定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log

_CONFIG_PATH = Path("config/heartbeat.json")


class ScheduleMode(str, Enum):
    """任务调度模式。"""

    HEARTBEAT = "heartbeat"
    """每 N 次心跳执行一次。"""

    SCHEDULED = "scheduled"
    """每天指定时间执行。"""

    MANUAL = "manual"
    """仅手动触发（Web / AI 工具）。"""


@dataclass
class TaskSchedule:
    """单个任务在心跳中的调度绑定。"""

    task_name: str
    mode: ScheduleMode = ScheduleMode.MANUAL
    every_n_beats: int = 10
    beat_count: int = 0
    schedule_times: List[str] = field(default_factory=list)
    last_run_date: str = ""
    model_id: str = ""
    """指定该调度使用的模型 ID，为空时使用任务定义或默认模型。"""
    reasoning_effort: str = ""
    """调度级思考等级覆盖，为空时使用任务定义或全局设置。"""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "task_name": self.task_name,
            "mode": self.mode.value,
        }
        if self.mode == ScheduleMode.HEARTBEAT:
            d["every_n_beats"] = self.every_n_beats
            d["beat_count"] = self.beat_count
        elif self.mode == ScheduleMode.SCHEDULED:
            d["schedule_times"] = self.schedule_times
            d["last_run_date"] = self.last_run_date
        if self.model_id:
            d["model_id"] = self.model_id
        if self.reasoning_effort:
            d["reasoning_effort"] = self.reasoning_effort
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskSchedule:
        mode = ScheduleMode(data.get("mode", "manual"))
        return cls(
            task_name=data["task_name"],
            mode=mode,
            every_n_beats=int(data.get("every_n_beats", 10)),
            beat_count=int(data.get("beat_count", 0)),
            schedule_times=list(data.get("schedule_times", [])),
            last_run_date=data.get("last_run_date", ""),
            model_id=data.get("model_id", ""),
            reasoning_effort=data.get("reasoning_effort", ""),
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
        """添加或更新任务调度绑定。"""
        for i, s in enumerate(self.task_schedules):
            if s.task_name == schedule.task_name:
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
        p = path or _CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Optional[Path] = None) -> HeartbeatConfig:
        p = path or _CONFIG_PATH
        if not p.exists():
            return _try_migrate()
        try:
            raw = json.loads(p.read_text("utf-8"))
            return _parse_config(raw)
        except Exception as exc:
            log(f"心跳配置加载失败，使用默认值: {exc}", "WARNING", tag="心跳")
            return cls()


def _parse_config(raw: Dict[str, Any]) -> HeartbeatConfig:
    schedules = [TaskSchedule.from_dict(s) for s in raw.get("task_schedules", [])]
    return HeartbeatConfig(
        enabled=raw.get("enabled", True),
        interval_seconds=int(raw.get("interval_seconds", 300)),
        analysis_temperature=float(raw.get("analysis_temperature", 0.7)),
        min_conversations_for_analysis=int(raw.get("min_conversations_for_analysis", 3)),
        task_schedules=schedules,
    )


def _try_migrate() -> HeartbeatConfig:
    """首次运行：尝试从旧 introspection.json 迁移。"""
    old_path = Path("config/introspection.json")
    if not old_path.exists():
        cfg = HeartbeatConfig()
        cfg.save()
        return cfg

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
            pass
        cfg.interval_seconds = old_interval

        reflect_hours = float(old.get("reflect_min_hours", 1.0))
        reflect_beats = max(1, int(reflect_hours * 3600 / cfg.interval_seconds))

        introspection_dir = Path("config/introspection")
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
                    pass

        cfg.task_schedules.append(TaskSchedule(
            task_name="self_reflection",
            mode=ScheduleMode.HEARTBEAT,
            every_n_beats=reflect_beats,
        ))

        cfg.save()
        old_path.rename(old_path.with_suffix(".json.bak"))
        log("从 introspection.json 迁移心跳配置完成", tag="心跳")
        return cfg
    except Exception as exc:
        log(f"心跳配置迁移失败: {exc}", "WARNING", tag="心跳")
        cfg = HeartbeatConfig()
        cfg.save()
        return cfg


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
