"""工作流服务 — Web 侧薄门面（engine 单例经 runtime 收口）。

启动/续跑/停止直达 WorkflowEngine，查询读 journal（断点恢复的事实
源）。规格校验错误以 ``WorkflowServiceError`` 上抛，路由层转 HTTP 400。
"""

from __future__ import annotations

from typing import Any, Dict, List

from services._runtime import get_runtime


class WorkflowServiceError(Exception):
    """请求非法（规格校验失败 / 状态不允许），message 直接面向用户。"""


class WorkflowRuntimeError(Exception):
    """运行时未就绪（引擎不可用），路由层映射 503。"""


def _engine():
    """解析工作流引擎（runtime 未就绪返回 None）。"""
    rt = get_runtime()
    if rt is None:
        return None
    return getattr(rt.mind, "workflow_engine", None)


class WorkflowService:

    async def start(self, spec: Dict[str, Any], *, scope: str = "",
                    resume_of: str = "") -> Dict[str, Any]:
        """启动工作流（后台执行）。"""
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        try:
            return await engine.start(spec, scope=scope, resume_of=resume_of)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    async def resume(self, run_id: str) -> Dict[str, Any]:
        """续跑已停止的运行。"""
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        try:
            return await engine.resume(run_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    def stop(self, run_id: str) -> Dict[str, Any]:
        """请求停止运行中的工作流。"""
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        result = engine.stop(run_id)
        if not result.get("ok"):
            raise WorkflowServiceError(str(result.get("error", "停止失败")))
        return result

    async def list_runs(self, limit: int = 30) -> List[Dict[str, Any]]:
        engine = _engine()
        return await engine.list_runs(limit) if engine is not None else []

    async def run_detail(self, run_id: str) -> Dict[str, Any]:
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        try:
            return await engine.run_detail(run_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc
