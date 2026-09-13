"""智能家居数据模型 — 设备状态快照、控制动作规格与统一错误。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from core.tool_errors import ErrorCause

# 无实质状态的占位状态值（渲染与统计时视为不可用）
INERT_STATES = ("unavailable", "unknown")


@dataclass
class DeviceState:
    """单台设备的实时状态快照（provider 维护的内存缓存单元）。"""

    entity_id: str
    """平台实体 ID（如 light.living_room）。"""
    domain: str
    """平台设备域（entity_id 前缀，如 light/climate）。"""
    name: str
    """展示名（friendly_name）。"""
    state: str
    """状态值（on/off/23.5/...）。"""
    attributes: Dict[str, Any] = field(default_factory=dict)
    """平台原始属性。"""
    area: str = ""
    """房间名（区域注册表解析，未分组为空串）。"""

    @property
    def available(self) -> bool:
        """设备当前是否有有效状态。"""
        return self.state not in INERT_STATES

    def to_dict(self) -> Dict[str, Any]:
        """可序列化快照（Web API / AI 工具共用）。"""
        return {
            "entity_id": self.entity_id,
            "domain": self.domain,
            "name": self.name,
            "state": self.state,
            "area": self.area,
            "available": self.available,
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class ActionSpec:
    """控制动作到平台服务调用的映射（域组件自声明，工具与面板共用校验）。

    value_param 为 None 表示该动作不接受值参数；convert 负责把外部传入的
    字符串值转换为服务数据类型，非法输入抛 ValueError。
    """

    service: str
    """平台服务名（如 turn_on）。"""
    description: str = ""
    """动作展示描述。"""
    value_param: Optional[str] = None
    """值参数映射到的服务数据键（None 为无参动作）。"""
    value_hint: str = ""
    """值参数语义提示（非法值报错与动作清单展示用）。"""
    convert: Optional[Callable[[str], Any]] = None
    """字符串值到服务数据类型的转换器。"""


class SmartHomeCallError(Exception):
    """智能家居调用错误（携带错误归因，AI 工具与 Web API 共用）。"""

    def __init__(self, message: str, cause: ErrorCause = ErrorCause.INTERNAL) -> None:
        super().__init__(message)
        self.cause = cause
