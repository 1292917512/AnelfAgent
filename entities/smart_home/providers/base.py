"""智能家居平台接入抽象 — 连接层 provider 基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar, Dict, List, Optional

from core.config import ConfigManager

from ..models import DeviceState


class SmartHomeProvider(ABC):
    """智能家居平台连接基类（一个平台一个子类，providers/ 目录自动发现注册）。

    子类职责：维护与平台的长连接（含自动重连），把全量/增量设备状态推入
    内存缓存，并经回调向 manager 汇报设备变更与连接状态。配置项声明后
    自动加 ``smart_home_<key>_`` 前缀注册进 ``entity/smart_home`` 配置组。
    """

    key: ClassVar[str] = ""
    """平台全局唯一标识（英文，作为配置键前缀的一部分）。"""
    display_name: ClassVar[str] = ""
    """平台展示名。"""
    config_schema: ClassVar[Dict[str, Dict[str, Any]]] = {}
    """平台连接配置项声明（键不含前缀，注册时自动加 ``smart_home_<key>_`` 前缀）。

    值格式与 ``core.config.register_configs`` 的 ConfigItem 一致。
    """

    def __init__(self) -> None:
        # manager 注入的事件回调（赋值即生效，未注入时静默跳过）
        self.on_device_update: Optional[Callable[[DeviceState], None]] = None
        """单台设备状态变更（增量）。"""
        self.on_sync: Optional[Callable[[], None]] = None
        """全量同步完成（消费方应重新拉取快照）。"""
        self.on_connection: Optional[Callable[[Dict[str, Any]], None]] = None
        """连接状态变化（载荷为 status()）。"""

    # ---- 配置 ----

    @classmethod
    def full_config_key(cls, name: str) -> str:
        """平台配置项的全局键（加实体前缀）。"""
        return f"smart_home_{cls.key}_{name}"

    def get_config(self, name: str, default: Any = None) -> Any:
        """读取平台配置项（未设置时回落 schema 声明的 default，再回落入参）。"""
        item = self.config_schema.get(name, {})
        fallback = item.get("default", default)
        return ConfigManager.get(self.full_config_key(name), fallback)

    # ---- 契约 ----

    @abstractmethod
    def is_configured(self) -> bool:
        """连接所需配置是否齐备（未齐备时 connect 不应被调用）。"""

    @abstractmethod
    async def connect(self) -> None:
        """启动托管连接（幂等；断线由实现自管重连，直到 close）。"""

    @abstractmethod
    async def close(self) -> None:
        """终止连接并释放资源（幂等；保留末次设备缓存供展示）。"""

    @abstractmethod
    def is_connected(self) -> bool:
        """当前是否已连上平台。"""

    @abstractmethod
    def snapshot(self) -> List[DeviceState]:
        """设备缓存快照（零 I/O）。"""

    @abstractmethod
    async def call_service(
        self, domain: str, service: str, entity_id: str, data: Dict[str, Any],
    ) -> None:
        """向平台发起设备服务调用（失败抛异常）。"""

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """连接状态的可序列化描述（面板/工具/上下文注入共用）。"""
