"""智能家居管理器 — 多供应商生命周期收口、设备聚合查询与控制归属路由。

Lifecycle 组件 ``smart_home_manager``：on_start 连接全部已配置 provider，
cleanup 逆序断开。设备状态缓存在各 provider 内（平台为唯一事实源），
本层做跨供应商聚合查询、按 entity_prefix 归属路由控制调用、
原生语音播报（speak）与域级服务调用的分派、SSE 订阅广播。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional

from core.log import log
from core.tool_errors import ErrorCause

from . import framework
from .models import DeviceState, SmartHomeCallError
from .providers import all_providers, get_provider
from .providers.base import SmartHomeProvider

_LOG_TAG = "智能家居"


class SmartHomeManager:
    """智能家居统一入口：多供应商状态查询 / 控制调用 / 实时事件订阅。"""

    def __init__(self) -> None:
        self._subscribers: List["asyncio.Queue[Dict[str, Any]]"] = []
        self._locks: Dict[str, asyncio.Lock] = {}
        self._reconnect_tasks: Dict[str, asyncio.Task[None]] = {}
        self._dirty: set[str] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---- Lifecycle 钩子 ----

    async def start(self) -> None:
        """启动：注入事件回调并连接全部已配置的 provider（未配置则待机）。"""
        providers = all_providers()
        self._loop = asyncio.get_running_loop()
        if not providers:
            log("智能家居平台连接未注册", "WARNING", tag=_LOG_TAG)
            return
        for provider in providers:
            self._wire(provider)
            if provider.is_configured():
                await provider.connect()
            else:
                log(
                    f"{provider.display_name} 未配置连接信息，待机",
                    "INFO", tag=_LOG_TAG,
                )

    async def close(self) -> None:
        """关停：断开全部 provider 连接。"""
        self._loop = None
        tasks = list(self._reconnect_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._reconnect_tasks.clear()
        self._dirty.clear()
        for provider in reversed(all_providers()):
            async with self.operation_lock(provider.key):
                try:
                    await provider.close()
                except Exception as exc:
                    log(f"{provider.display_name} 关闭失败: {exc}", "WARNING", tag=_LOG_TAG)

    def operation_lock(self, key: str) -> asyncio.Lock:
        """同一平台的连接、控制与凭据轮换共享串行边界。"""
        return self._locks.setdefault(key, asyncio.Lock())

    def request_reconnect(self, key: str) -> None:
        """合并配置通知到宿主循环，兼容来自工作线程的配置变更。"""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._schedule_reconnect, key)

    def _schedule_reconnect(self, key: str) -> None:
        if self._loop is None:
            return
        self._dirty.add(key)
        if key not in self._reconnect_tasks:
            self._reconnect_tasks[key] = asyncio.create_task(self._drain_reconnects(key))

    async def _drain_reconnects(self, key: str) -> None:
        try:
            while key in self._dirty:
                await asyncio.sleep(0.15)
                self._dirty.discard(key)
                try:
                    await self.reconnect(key)
                except Exception as exc:
                    log(f"{key} 配置重连失败: {exc}", "WARNING", tag=_LOG_TAG)
        finally:
            self._reconnect_tasks.pop(key, None)

    async def reconnect(self, key: str = "") -> None:
        """重连指定 provider（空为全部；连接配置变更后按新配置重建）。"""
        targets = [get_provider(key)] if key else all_providers()
        for provider in targets:
            if provider is None:
                continue
            async with self.operation_lock(provider.key):
                await provider.close()
                self._wire(provider)
                if provider.is_configured():
                    await provider.connect()
                self._broadcast({"event": "connection", "status": provider.status()})

    # ---- 供应商归属 ----

    def owner_of(self, entity_id: str) -> Optional[SmartHomeProvider]:
        """按 entity_prefix 解析设备所属 provider（空前缀平台兜底）。"""
        fallback: Optional[SmartHomeProvider] = None
        for provider in all_providers():
            if not provider.entity_prefix:
                fallback = provider
                continue
            if entity_id.startswith(provider.entity_prefix):
                return provider
        return fallback

    # ---- 查询 ----

    def status(self) -> Dict[str, Any]:
        """全供应商连接状态（面板/工具/上下文注入共用）。"""
        providers = [p.status() for p in all_providers()]
        return {
            "providers": providers,
            "configured": any(p["configured"] for p in providers),
            "connected": any(p["connected"] for p in providers),
            "device_count": sum(p["device_count"] for p in providers),
        }

    def devices(
        self, domain: str = "", area: str = "", name: str = "",
    ) -> List[DeviceState]:
        """设备快照聚合过滤查询（domain 支持组件 key 或平台域；area 精确；name 模糊）。"""
        result: List[DeviceState] = []
        for provider in all_providers():
            max_age = provider.max_state_age()
            for device in provider.snapshot():
                stale = (device.stale or not provider.is_connected()
                         or (max_age is not None and time.time() - device.updated_at > max_age))
                result.append(replace(device, provider=provider.key, stale=stale))
        domain = domain.strip().lower()
        if domain:
            result = [
                d for d in result
                if d.domain == domain
                or ((device_domain := framework.domain_for_ha(d.domain)) is not None
                    and device_domain.key == domain)
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

    def describe_device(self, device: DeviceState) -> Dict[str, Any]:
        """设备事实与能力的单一出站口（查询、SSE、AI 工具共用）。"""
        provider = self.owner_of(device.entity_id)
        actions = provider.actions_for(device) if provider is not None else {}
        return {**device.to_dict(), "actions": {key: spec.to_dict() for key, spec in actions.items()}}

    # ---- 控制 ----

    async def call_service(
        self, entity_id: str, action: str, raw_value: str = "",
    ) -> Dict[str, Any]:
        """将同一平台的控制与重连串行化，避免命令发送期间连接被关闭。"""
        provider = self.owner_of(entity_id.strip())
        async with self.operation_lock(provider.key if provider is not None else ""):
            return await self._call_service(entity_id, action.strip(), raw_value)

    async def _call_service(
        self, entity_id: str, action: str, raw_value: str,
    ) -> Dict[str, Any]:
        """控制设备：归属/配置/设备/域/动作逐层校验后分派调用。"""
        entity_id = entity_id.strip()
        action = action.strip()
        provider = self.owner_of(entity_id)
        if provider is None or not provider.is_configured():
            raise SmartHomeCallError(
                "设备所属平台未配置连接（请在实体配置中完善对应供应商的连接信息）",
                ErrorCause.CONFIG,
            )
        device = self.get_device(entity_id)
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
        if not provider.is_connected():
            raise SmartHomeCallError(
                f"未连接到{provider.display_name}（等待自动重连或检查连接配置）",
                ErrorCause.NETWORK,
            )
        if not device.available:
            raise SmartHomeCallError("设备离线或状态已过期，请待状态恢复后重试", ErrorCause.STATE)
        spec = provider.actions_for(device).get(action)
        if spec is None:
            raise SmartHomeCallError(f"设备不支持动作: {action}", ErrorCause.PARAM)
        if spec.options and raw_value.strip() not in spec.options:
            raise SmartHomeCallError(f"设备支持的值: {', '.join(spec.options)}", ErrorCause.PARAM)
        # 语音播报优先走供应商原生 TTS 通道，不支持则回落域级服务调用
        # （如 HA 经 tts_service 配置跨域调用 tts.*_say）
        if action.strip() == "speak" and provider.supports_speak(device):
            text = raw_value.strip()
            if not text:
                raise SmartHomeCallError(
                    "动作 speak 需要 value 参数（播报文本）", ErrorCause.PARAM,
                )
            try:
                await provider.speak(device, text)
            except SmartHomeCallError:
                raise
            except TimeoutError as exc:
                raise SmartHomeCallError("播报超时，结果未知，请勿立即重复播报", ErrorCause.TIMEOUT) from exc
            except Exception as exc:
                raise SmartHomeCallError(
                    f"播报失败: {exc}", ErrorCause.NETWORK,
                ) from exc
            return {
                "entity_id": device.entity_id,
                "name": device.name,
                "action": "speak",
                "service": f"{provider.key}.speak",
                "status": "accepted",
            }
        call = domain.build_service_call(action, raw_value, device)
        ha_domain = call.ha_domain or device.domain
        try:
            await provider.call_service(
                ha_domain, call.service, device.entity_id, call.data,
            )
        except SmartHomeCallError:
            raise
        except (ValueError, TypeError) as exc:
            raise SmartHomeCallError(f"控制参数不受支持: {exc}", ErrorCause.PARAM) from exc
        except TimeoutError as exc:
            raise SmartHomeCallError("控制超时，结果未知，请先查询状态再决定是否重试", ErrorCause.TIMEOUT) from exc
        except Exception as exc:
            raise SmartHomeCallError(
                f"控制失败: {exc}", ErrorCause.NETWORK,
            ) from exc
        return {
            "entity_id": device.entity_id,
            "name": device.name,
            "action": action.strip(),
            "service": f"{ha_domain}.{call.service}",
            "status": "accepted",
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
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"event": "sync"})

    def _wire(self, provider: SmartHomeProvider) -> None:
        """把 provider 事件回调接入订阅广播。"""
        def updated(device: DeviceState) -> None:
            current = self.get_device(device.entity_id)
            if current is not None:
                self._broadcast({"event": "state", "device": self.describe_device(current)})

        provider.on_device_update = updated
        provider.on_sync = lambda: self._broadcast({"event": "sync"})
        provider.on_connection = lambda s: self._broadcast(
            {"event": "connection", "status": s},
        )


_MANAGER = SmartHomeManager()


def get_smart_home_manager() -> SmartHomeManager:
    """智能家居管理器单例。"""
    return _MANAGER
