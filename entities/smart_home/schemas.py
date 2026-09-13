"""智能家居 Web API 出入站模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ControlRequest(BaseModel):
    """设备控制请求（面板控制按钮）。"""

    entity_id: str = Field(..., description="设备实体 ID")
    action: str = Field(..., description="控制动作")
    value: str = Field("", description="动作参数")


class DeviceOut(BaseModel):
    """设备状态出站模型。"""

    entity_id: str
    domain: str
    name: str
    state: str
    area: str
    available: bool
    attributes: Dict[str, Any]


class DevicesResult(BaseModel):
    """设备清单出站模型。"""

    connection: Dict[str, Any]
    devices: List[DeviceOut]
    count: int


class StatusResult(BaseModel):
    """连接状态出站模型。"""

    provider: Optional[str]
    provider_name: str
    configured: bool
    connected: bool
    device_count: int
    last_error: Optional[str]
    connected_at: Optional[float]
