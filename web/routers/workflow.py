"""工作流 API 路由。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.workflow import (
    WorkflowRuntimeError,
    WorkflowService,
    WorkflowServiceError,
)

router = APIRouter(prefix="/workflow", tags=["workflow"])

_wf_svc = WorkflowService()


class StartRunRequest(BaseModel):
    """启动请求：spec 为工作流规格对象；resume_of 为修订源 run_id。"""

    spec: Dict[str, Any]
    scope: str = ""
    resume_of: str = ""


@router.get("/runs")
async def list_runs(limit: int = 30) -> Dict[str, Any]:
    return {"runs": await _wf_svc.list_runs(limit)}


@router.post("/runs")
async def start_run(req: StartRunRequest) -> Dict[str, Any]:
    try:
        return await _wf_svc.start(req.spec, scope=req.scope, resume_of=req.resume_of)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/runs/{run_id}")
async def run_detail(run_id: str) -> Dict[str, Any]:
    try:
        return await _wf_svc.run_detail(run_id)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str) -> Dict[str, Any]:
    try:
        return _wf_svc.stop(run_id)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str) -> Dict[str, Any]:
    try:
        return await _wf_svc.resume(run_id)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(400, str(e)) from e
