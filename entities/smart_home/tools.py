"""智能家居 AI 工具 — 设备查询 / 设备控制 / 设备域组件管理。"""

from __future__ import annotations

import json
from typing import Union

from core.config import ConfigRegistry
from core.tool_errors import ErrorCause
from entities._sdk import save_config_value, tool, tool_error

from . import framework
from .manager import get_smart_home_manager
from .models import SmartHomeCallError


@tool(name="smart_home_devices", group="smart_home", concurrency_safe=True)
def smart_home_devices(domain: str = "", area: str = "", name: str = "") -> str:
    """查询智能家居设备与实时状态（含平台连接状态），可按设备域、房间、名称过滤。

    Args:
        domain: 设备域过滤（组件 key 如 light、climate、sensor；留空查全部）
        area: 房间过滤（精确匹配区域名，如 客厅；留空查全部）
        name: 名称/实体 ID 模糊过滤（定位单台设备时用；留空查全部）
    """
    manager = get_smart_home_manager()
    status = manager.status()
    devices = manager.devices(domain=domain, area=area, name=name)
    # 批量清单裁剪原始属性（控制 token），精确过滤少量设备时保留完整属性
    slim = len(devices) > 5
    items = []
    for device in devices:
        item = device.to_dict()
        if slim:
            item.pop("attributes", None)
        items.append(item)
    return json.dumps({
        "connection": status,
        "devices": items,
        "count": len(items),
        "hint": None if status.get("connected") or not status.get("configured")
        else "当前未连接，设备状态为最后一次同步的快照",
    }, ensure_ascii=False, default=str)


@tool(name="smart_home_control", group="smart_home", risk="MEDIUM")
async def smart_home_control(entity_id: str, action: str, value: str = "") -> str:
    """控制智能家居设备（开关灯/调亮度/调温度/开关窗帘/播放控制/激活场景等）。

    Args:
        entity_id: 设备实体 ID（如 light.living_room，经 smart_home_devices 查询获得）
        action: 控制动作（如 turn_on/turn_off/toggle/set_brightness/set_temperature/set_hvac_mode/open_cover/close_cover/stop_cover/set_position/media_play/media_pause/set_volume/activate；各设备域可用动作经 smart_home_domains 查询）
        value: 动作参数（需要值的动作必填：亮度 0-100、温度摄氏度、窗帘位置 0-100、音量 0-100、模式 cool/heat 等）
    """
    manager = get_smart_home_manager()
    try:
        result = await manager.call_service(entity_id, action, value)
    except SmartHomeCallError as exc:
        return tool_error(str(exc), cause=exc.cause)
    return json.dumps({"success": True, **result}, ensure_ascii=False)


@tool(name="smart_home_domains", group="smart_home", concurrency_safe=True)
def smart_home_domains(
    action: str = "list", domain: str = "", name: str = "", value: str = "",
) -> str:
    """管理智能家居设备域组件：list 查看组件综合信息（启停状态/设备数/可用控制动作/配置项），preview 预览上下文注入内容，enable/disable 启停组件，set_config 修改组件配置。

    Args:
        action: 操作类型：list（默认）/ preview / enable / disable / set_config
        domain: 设备域 key（enable/disable/set_config 时必填，如 light、climate、sensor）
        name: set_config 时的配置项短名（如 inject_limit）
        value: 配置值（set_config 时必填）
    """
    action = action.strip().lower()

    if action == "list":
        manager = get_smart_home_manager()
        devices = manager.devices()
        domains = [d.describe(devices) for d in framework.all_domains()]
        return json.dumps(
            {"connection": manager.status(), "domains": domains,
             "count": len(domains)},
            ensure_ascii=False, default=str,
        )

    if action == "preview":
        manager = get_smart_home_manager()
        content = framework.render_context(manager.devices())
        return json.dumps(
            {"injecting": bool(content), "content": content or None},
            ensure_ascii=False,
        )

    if action in ("enable", "disable"):
        target = _resolve_domain(domain)
        if isinstance(target, str):
            return target
        enabled = action == "enable"
        save_config_value(target.enabled_key, enabled)
        return json.dumps(
            {"success": True, "domain": target.key, "enabled": enabled},
            ensure_ascii=False,
        )

    if action == "set_config":
        target = _resolve_domain(domain)
        if isinstance(target, str):
            return target
        config_name = name.strip()
        if not config_name or config_name not in target.config_schema:
            known = list(target.config_schema.keys())
            return tool_error(
                f"设备域 {target.key} 没有配置项: {config_name or '(未提供)'}"
                f"（可用: {', '.join(known)}）",
                cause=ErrorCause.PARAM,
            )
        # 经统一配置架构的类型矫正与边界收敛（与 Web/配置中心同纪律）
        full_key = target.full_config_key(config_name)
        item = ConfigRegistry.get_item(full_key)
        coerced: object = value
        if item is not None:
            try:
                coerced = item.clamp(item.coerce_value(value))
            except ValueError as exc:
                return tool_error(str(exc), cause=ErrorCause.PARAM)
        save_config_value(full_key, coerced)
        return json.dumps(
            {"success": True, "domain": target.key, "name": config_name,
             "value": coerced},
            ensure_ascii=False, default=str,
        )

    return tool_error(
        f"未知操作: {action}（可用: list / preview / enable / disable / set_config）",
        cause=ErrorCause.PARAM,
    )


def _resolve_domain(key: str) -> Union["framework.DeviceDomain", str]:
    """解析设备域 key，失败返回 tool_error 文本。"""
    if not key.strip():
        return tool_error("需要提供 domain 参数", cause=ErrorCause.PARAM)
    target = framework.get_domain(key.strip())
    if target is None:
        known = [d.key for d in framework.all_domains()]
        return tool_error(
            f"未找到设备域: {key}（可用: {', '.join(known)}）",
            cause=ErrorCause.NOT_FOUND,
        )
    return target
