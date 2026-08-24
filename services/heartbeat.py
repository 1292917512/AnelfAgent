"""心跳系统服务 -- 心跳配置读写、引擎状态查询与手动触发。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from core.log import log
from services._runtime import get_runtime


class HeartbeatServiceError(Exception):
    """心跳服务错误（status_code 供路由层映射 HTTP 状态码）。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class HeartbeatService:
    """心跳调度管理服务（Web 侧入口）。"""

    @staticmethod
    def get_config() -> Dict[str, Any]:
        """返回心跳调度配置。"""
        from agent.heartbeat.config import get_heartbeat_config
        return get_heartbeat_config().to_dict()

    @staticmethod
    def save_config(params: Dict[str, Any]) -> None:
        """保存心跳配置并热重载引擎。

        Args:
            params: 仅含显式提供字段的更新字典（enabled / interval_seconds /
                analysis_temperature / min_conversations_for_analysis / task_schedules）。

        Raises:
            HeartbeatServiceError: task_schedules 校验失败（400）。
        """
        from agent.heartbeat.config import (
            TaskSchedule,
            get_heartbeat_config,
            validate_schedules,
        )

        cfg = get_heartbeat_config()
        if "enabled" in params:
            cfg.enabled = params["enabled"]
        if "interval_seconds" in params:
            cfg.interval_seconds = max(10, params["interval_seconds"])
        if "analysis_temperature" in params:
            cfg.analysis_temperature = params["analysis_temperature"]
        if "min_conversations_for_analysis" in params:
            cfg.min_conversations_for_analysis = params["min_conversations_for_analysis"]
        if "task_schedules" in params:
            schedules = [TaskSchedule.from_dict(s) for s in params["task_schedules"]]
            if err := validate_schedules(schedules):
                raise HeartbeatServiceError(err, status_code=400)
            cfg.task_schedules = schedules
        cfg.save()

        rt = get_runtime()
        if rt is not None:
            rt.mind.heartbeat_engine.reload()

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

        async def _run() -> None:
            try:
                executed = await rt.mind.heartbeat_engine.tick()
                log(f"Web 手动心跳完成: 执行了 {len(executed)} 个任务", tag="心跳")
            except Exception as exc:
                log(f"Web 手动心跳异常: {exc}", "WARNING", tag="心跳")

        asyncio.create_task(_run(), name="agent.heartbeat.web_manual_tick")

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
