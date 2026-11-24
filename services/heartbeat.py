"""心跳系统服务 -- 心跳配置读写、引擎状态查询与手动触发。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict

from core.log import log
from services._runtime import get_runtime

if TYPE_CHECKING:
    from agent.heartbeat.config import HeartbeatConfig, TaskSchedule


class HeartbeatServiceError(Exception):
    """心跳服务错误（status_code 供路由层映射 HTTP 状态码）。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _schedule_table(rows: list[dict[str, Any]]) -> dict[str, TaskSchedule]:
    from agent.heartbeat.config import TaskSchedule

    schedules: dict[str, TaskSchedule] = {}
    for row in rows:
        try:
            item = TaskSchedule.from_dict(row)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise HeartbeatServiceError(f"任务调度配置无效: {exc}") from exc
        if not isinstance(item.task_name, str) or not item.task_name or item.task_name in schedules:
            raise HeartbeatServiceError("调度任务名称必须有效且唯一")
        schedules[item.task_name] = item
    return schedules


def _definition(schedule: TaskSchedule | None) -> dict[str, Any] | None:
    if schedule is None:
        return None
    definition = schedule.to_dict()
    definition.pop("beat_count", None)
    return definition


def _merge_schedules(current: list[TaskSchedule], params: dict[str, Any]) -> list[TaskSchedule]:
    """按任务对比编辑基线和目标定义，提交前保留实时计数并校验合并结果。"""
    from agent.heartbeat.config import ScheduleMode, validate_schedules

    if "base_task_schedules" not in params or "task_schedules" not in params:
        raise HeartbeatServiceError("请重新加载调度配置并随修改提交编辑基线", status_code=428)
    baseline = _schedule_table(params["base_task_schedules"])
    desired = _schedule_table(params["task_schedules"])
    if error := validate_schedules(list(desired.values())):
        raise HeartbeatServiceError(error)
    resets = set(params.get("reset_schedule_counters", []))
    counter_modes = (ScheduleMode.HEARTBEAT, ScheduleMode.IDLE)
    if any(name not in desired or desired[name].mode not in counter_modes for name in resets):
        raise HeartbeatServiceError("只能重置计数型调度的节拍")
    merged = {s.task_name: s for s in current}
    for name in dict.fromkeys([*baseline, *desired]):
        before = _definition(baseline.get(name))
        replacement = desired.get(name)
        after = _definition(replacement)
        if before == after and name not in resets:
            continue
        live = merged.get(name)
        actual = _definition(live)
        if actual != before and (actual != after or name in resets):
            raise HeartbeatServiceError(f"任务调度已被其他编辑修改: {name}", status_code=409)
        if replacement is None:
            merged.pop(name, None)
        else:
            replacement.beat_count = (
                live.beat_count if live and live.mode in counter_modes and replacement.mode in counter_modes
                and name not in resets else 0
            )
            merged[name] = replacement
    schedules = list(merged.values())
    if error := validate_schedules(schedules):
        raise HeartbeatServiceError(error, status_code=409)
    return schedules


class HeartbeatService:
    """心跳调度管理服务（Web 侧入口）。"""

    @staticmethod
    def get_config() -> Dict[str, Any]:
        """返回心跳调度配置。

        interval_seconds 为单一真源 mind.heartbeat_interval 的派生视图
        （heartbeat.json 不存间隔副本），与配置中心展示恒一致。
        """
        from agent.heartbeat.config import current_interval_seconds, locked_heartbeat_config

        rt = get_runtime()
        engine = getattr(getattr(rt, "mind", None), "heartbeat_engine", None)
        with locked_heartbeat_config() as cfg:
            return {
                **cfg.to_dict(),
                "interval_seconds": current_interval_seconds(),
                "reload_pending": getattr(engine, "reload_pending", False) is True,
            }

    @staticmethod
    def _parse_interval_seconds(value: Any) -> float:
        """按配置中心的注册项转换与限制心跳间隔。"""
        from core.config import ConfigRegistry

        item = ConfigRegistry.get_item("heartbeat_interval")
        try:
            parsed = item.clamp(item.coerce_value(value)) if item else max(60.0, float(value))
        except (TypeError, ValueError):
            raise HeartbeatServiceError(f"心跳间隔取值非法: {value}", status_code=400) from None
        return float(parsed)

    @staticmethod
    def save_config(params: Dict[str, Any]) -> bool:
        """保存心跳配置并热重载引擎。

        Args:
            params: 仅含显式提供的配置字段。调度更新同时提供 base_task_schedules 和
                task_schedules，可用 reset_schedule_counters 显式清零。
                interval_seconds 路由到 mind.heartbeat_interval（单一真源），
                其余字段落 heartbeat.json。

        Returns:
            配置已保存且引擎无需重试时为 True，已保存但应用失败时为 False。

        Raises:
            HeartbeatServiceError: 参数非法（400）、调度冲突（409）、缺少基线（428）或写入失败（503）。
        """
        from agent.config import get_config_provider
        from agent.heartbeat.config import locked_heartbeat_config

        interval = HeartbeatService._parse_interval_seconds(params["interval_seconds"]) if "interval_seconds" in params else None
        try:
            if params.keys() <= {"interval_seconds"}:
                if interval is not None:
                    get_config_provider().save_mind_config(heartbeat_interval=interval)
                return True
            with locked_heartbeat_config() as cfg:
                changed = HeartbeatService._commit_config(cfg, params, interval)
        except ValueError as exc:
            raise HeartbeatServiceError(str(exc)) from exc
        except OSError as exc:
            raise HeartbeatServiceError("心跳配置写入失败，请重试", status_code=503) from exc
        if changed:
            try:
                HeartbeatService.reload_engine()
            except HeartbeatServiceError:
                return False
        return True

    @staticmethod
    def _commit_config(cfg: HeartbeatConfig, params: dict[str, Any], interval: float | None) -> bool:
        from agent.config import get_config_provider

        schedule_keys = {"task_schedules", "base_task_schedules", "reset_schedule_counters"}
        schedules = _merge_schedules(cfg.task_schedules, params) if schedule_keys.intersection(params) else None

        updated = replace(
            cfg,
            enabled=params.get("enabled", cfg.enabled),
            analysis_temperature=params.get("analysis_temperature", cfg.analysis_temperature),
            min_conversations_for_analysis=params.get("min_conversations_for_analysis", cfg.min_conversations_for_analysis),
            task_schedules=cfg.task_schedules if schedules is None else schedules,
        )
        if updated == cfg:
            if interval is not None:
                get_config_provider().save_mind_config(heartbeat_interval=interval)
            return False
        if interval is None:
            updated.save()
            cfg.apply(updated)
        else:
            updated.save_with_interval(cfg, interval)

        return True

    @staticmethod
    def reload_engine() -> None:
        """重试应用磁盘上的调度配置，已提交的编辑和计数重置无需重放。"""
        engine = getattr(getattr(get_runtime(), "mind", None), "heartbeat_engine", None)
        if engine is not None:
            try:
                engine.reload()
            except Exception as exc:
                log(f"心跳配置已保存，引擎热重载失败: {exc}", "WARNING", tag="心跳")
                raise HeartbeatServiceError("心跳配置已保存，热重载失败，可重试应用", status_code=503) from exc

    @staticmethod
    def get_status() -> Dict[str, Any]:
        """返回心跳引擎运行状态。"""
        rt = get_runtime()
        if rt is None:
            return {"enabled": False, "total_ticks": 0, "message": "Agent 尚未初始化"}
        return rt.mind.heartbeat_engine.get_status()

    @staticmethod
    def trigger() -> None:
        """手动触发一次心跳（后台异步执行）。

        Raises:
            HeartbeatServiceError: Agent 未初始化（503）。
        """
        rt = get_runtime()
        if rt is None:
            raise HeartbeatServiceError("Agent 尚未初始化", status_code=503)

        future = rt.mind.driver.submit_tick()

        def _log_done(done: "asyncio.Future[list[str]]") -> None:
            if done.cancelled():
                return
            try:
                executed = done.result()
                log(f"Web 手动心跳完成: 执行了 {len(executed)} 个任务", tag="心跳")
            except Exception as exc:
                log(f"Web 手动心跳异常: {exc}", "WARNING", tag="心跳")

        future.add_done_callback(_log_done)

    @staticmethod
    def remove_schedule_for_task(task_name: str) -> None:
        """移除指定任务的心跳调度绑定并热重载引擎（任务删除后的同步清理）。"""
        try:
            from agent.heartbeat.config import get_heartbeat_config
            cfg = get_heartbeat_config()
            if cfg.remove_schedule(task_name):
                cfg.save()
        except Exception as e:
            log(f"心跳调度同步移除失败 [{task_name}]: {e}", "WARNING")
        try:
            rt = get_runtime()
            if rt is not None:
                rt.mind.heartbeat_engine.reload()
        except Exception as e:
            log(f"心跳引擎热重载失败 [{task_name}]: {e}", "DEBUG")
