"""服务端代理与路由采样诊断。"""

from fastapi import APIRouter

from services.network import NetworkProbeReport, NetworkProbeRequest, diagnose_network

router = APIRouter(prefix="/network", tags=["network"])


@router.post("/diagnose", response_model=NetworkProbeReport)
async def diagnose(request: NetworkProbeRequest) -> NetworkProbeReport:
    """按已生效配置检测连接，不写配置、不跟随重定向。"""
    return await diagnose_network(request)
