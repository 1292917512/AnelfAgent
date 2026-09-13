"""智能家居管理器 — provider 生命周期收口、设备查询与实时事件广播。

Lifecycle 组件 ``smart_home_manager``：on_start 按配置启动连接，cleanup
断开。设备状态缓存在 provider 内（平台为唯一事实源），本层只做过滤查询、
控制调用的域路由与校验、SSE 订阅广播。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from core.log import log
from core.tool_errors import ErrorCause

from . import framework
from .models import DeviceState, SmartHomeCallError
from .providers import active_provider
from .providers.base import SmartHomeProvider

_LOG_TAG = "智能家居"


class SmartHomeManager:
    """智能家居统一入口：状态查询 / 控制调用 / 实时事件订阅。"""

    def __init__(self) -> None:
        self._subscribers: List["asyncio.Queue[Dict[str, Any]]"] = []

    # ---- Lifecycle 钩子 ----

    async def start(self) -> None:
        """启动：注入事件回调并按配置连接（未配置则待机）。"""
        provider = self._require_provider()
        self._wire(provider)
        if provider.is_configured():
            await provider.connect()
        else:
            log(
                "未配置 Home Assistant 连接（smart_home_ha_url/token），智能家居待机",
                "INFO", tag=_LOG_TAG,
            )

    async def close(self) -> None:
        """关停：断开平台连接。"""
        provider = active_provider()
        if provider is not None:
            await provider.close()

    async def reconnect(self) -> None:
        """连接配置变更后按新配置重建连接。"""
        provider = active_provider()
        if provider is None:
            return
        await provider.close()
        self._wire(provider)
        if provider.is_configured():
            await provider.connect()

    # ---- 查询 ----

    def _require_provider(self) -> SmartHomeProvider:
        provider = active_provider()
        if provider is None:
            raise SmartHomeCallError("智能家居平台连接未注册", ErrorCause.STATE)
        return provider

    def status(self) -> Dict[str, Any]:
        """连接状态（面板/工具/上下文注入共用）。"""
        provider = active_provider()
        if provider is None:
            return {
                "provider": None, "provider_name": "", "configured": False,
                "connected": False, "device_count": 0,
                "last_error": "智能家居平台连接未注册", "connected_at": None,
            }
        return provider.status()

    def devices(
        self, domain: str = "", area: str = "", name: str = "",
    ) -> List[DeviceState]:
        """设备快照过滤查询（domain 支持组件 key 或平台域；area 精确；name 模糊）。"""
        provider = active_provider()
        if provider is None:
            return []
        result = provider.snapshot()
        domain = domain.strip().lower()
        if domain:
            result = [
                d for d in result
                if d.domain == domain
                or (framework.domain_for_ha(d.domain) is not None
                    and framework.domain_for_ha(d.domain).key == domain)
            ]
        area = area.strip()
        if area:
            result = [d for d in result if d.area == area]
        name = name.strip()
        if name:
            result = [d for d in result if name in d.name or name in d.entity_id]
        return result

    def get_device(self, entity_id: str) -> Optional[DeviceState]:
        """按实体 ID 取设备快照。"""
        for device in self.devices():
            if device.entity_id == entity_id:
                return device
        return None

    # ---- 控制 ----

    async def call_service(
        self, entity_id: str, action: str, raw_value: str = "",
    ) -> Dict[str, Any]:
        """控制设备：配置/设备/域/动作逐层校验后委托 provider 调用。"""
        provider = self._require_provider()
        if not provider.is_configured():
            raise SmartHomeCallError(
                "未配置 Home Assistant 连接（请在实体配置中填写 "
                "smart_home_ha_url / smart_home_ha_token）",
                ErrorCause.CONFIG,
            )
        device = self.get_device(entity_id.strip())
        if device is None:
            raise SmartHomeCallError(
                f"设备不存在: {entity_id or '(未提供)'}"
                "（经 smart_home_devices 查询可用实体 ID）",
                ErrorCause.NOT_FOUND,
            )
        domain = framework.domain_for_ha(device.domain)
        if domain is None:
            raise SmartHomeCallError(
                f"设备 {device.entity_id} 的类型（{device.domain}）没有对应设备域组件",
                ErrorCause.NOT_FOUND,
            )
        if not domain.is_enabled():
            raise SmartHomeCallError(
                f"设备域「{domain.display_name}」已禁用（可经 smart_home_domains 启用）",
                ErrorCause.STATE,
            )
        service, service_data = domain.build_service_call(action, raw_value)
        if not provider.is_connected():
            raise SmartHomeCallError(
                "未连接到 Home Assistant（等待自动重连或检查连接配置）",
                ErrorCause.NETWORK,
            )
        try:
            await provider.call_service(
                device.domain, service, device.entity_id, service_data,
            )
        except Exception as exc:
            raise SmartHomeCallError(
                f"控制失败: {exc}", ErrorCause.NETWORK,
            ) from exc
        return {
            "entity_id": device.entity_id,
            "name": device.name,
            "action": action.strip(),
            "service": f"{device.domain}.{service}",
        }

    # ---- 订阅广播（SSE 消费） ----

    def subscribe(self) -> "asyncio.Queue[Dict[str, Any]]":
        """注册事件订阅者队列（设备变更/全量同步/连接状态）。"""
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=256)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        """注销事件订阅者队列。"""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _broadcast(self, payload: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                log("智能家居事件订阅队列已满，丢弃事件", "DEBUG", tag=_LOG_TAG)

    def _wire(self, provider: SmartHomeProvider) -> None:
        """把 provider 事件回调接入订阅广播。"""
        provider.on_device_update = lambda d: self._broadcast(
            {"event": "state", "device": d.to_dict()},
        )
        provider.on_sync = lambda: self._broadcast({"event": "sync"})
        provider.on_connection = lambda s: self._broadcast(
            {"event": "connection", "status": s},
        )


_MANAGER = SmartHomeManager()


def get_smart_home_manager() -> SmartHomeManager:
    """智能家居管理器单例。"""
    return _MANAGER
