"""智能家居 AI 工具 — 设备查询 / 设备控制 / 设备域组件管理 / 小度令牌续期。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Union

from core.config import ConfigManager, ConfigRegistry
from core.log import log
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


# ---- 小度（DuerOS）令牌续期 ----

BAIDU_TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"
_XIAODU_MCP_SERVERS = ("xiaodu-iot", "xiaodu")


def _xiaodu_cfg(name: str) -> str:
    """读小度凭据配置（服务端原文，不进 LLM 上下文）。"""
    return str(ConfigManager.get(f"smart_home_xiaodu_{name}", "") or "").strip()


def _xiaodu_auth_url(app_key: str) -> str:
    """百度 oob 授权码流程链接（无回调服务，页面直接显示 code）。"""
    return (
        "https://openapi.baidu.com/oauth/2.0/authorize"
        f"?response_type=code&client_id={app_key}&redirect_uri=oob&scope=basic,dueros"
    )


async def _baidu_token_request(params: Dict[str, str]) -> Dict[str, Any]:
    """调百度 OAuth token 端点（独立函数便于测试替换）。"""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(BAIDU_TOKEN_URL, data=params) as resp:
            return json.loads(await resp.text())


def _mcp_store():
    """挂接 bridge 热重载的 MCP 配置存取实例（mcp 管理工具同款模式）。"""
    from entities.mcp.bridge import get_mcp_bridge
    from entities.mcp.config import MCPServerStore

    def _reload() -> None:
        try:
            bridge = get_mcp_bridge()
            if bridge is not None:
                bridge.reload_config()
        except Exception as exc:
            log(f"MCP 配置热重载失败: {exc}", "WARNING")

    return MCPServerStore(on_reload=_reload)


@tool(name="xiaodu_refresh_token", group="smart_home", risk="MEDIUM")
async def xiaodu_refresh_token(code: str = "") -> str:
    """刷新或换取小度（DuerOS）双 MCP 共用的 ACCESS_TOKEN，回写配置并热重载生效。

    调用时机：xiaodu / xiaodu-iot MCP 连接异常（401/unauthorized 类错误）、
    临近 smart_home_xiaodu_token_expires_at、或用户给了新授权码。应用信息
    （AppKey/SecretKey/refresh_token）读自 entity/smart_home 配置组（配置中心
    可填、AI 可经 update_entity_config 写）；密钥与令牌不进返回值。

    Args:
        code: 百度 OAuth 授权码（首接或 SecretKey 重置后用；留空则用配置中的 refresh_token 续期）
    """
    app_key, secret = _xiaodu_cfg("app_key"), _xiaodu_cfg("secret_key")
    refresh = _xiaodu_cfg("refresh_token")
    code = code.strip()
    if not app_key or not secret:
        return tool_error(
            "小度应用信息未配置（smart_home_xiaodu_app_key / secret_key）",
            cause=ErrorCause.CONFIG,
            hint="在 DuerOS MCP 控制台创建应用后，经配置中心或 update_entity_config 填入",
        )
    if not code and not refresh:
        return tool_error(
            "无可用 refresh_token 且未提供授权码",
            cause=ErrorCause.CONFIG,
            hint=f"请用户打开授权链接授权后回传页面授权码: {_xiaodu_auth_url(app_key)}",
        )
    params = {
        "grant_type": "authorization_code" if code else "refresh_token",
        "client_id": app_key,
        "client_secret": secret,
        "redirect_uri": "oob",
    }
    if code:
        params["code"] = code
    else:
        params["refresh_token"] = refresh
    try:
        data = await _baidu_token_request(params)
    except Exception as exc:
        return tool_error(
            f"百度 token 端点调用失败: {exc}", cause=ErrorCause.NETWORK,
        )
    access = str(data.get("access_token") or "")
    if not access:
        return tool_error(
            "百度授权失败: "
            f"{data.get('error_description') or data.get('error') or data}",
            cause=ErrorCause.PERMISSION,
            hint=f"refresh_token 失效时请重新授权: {_xiaodu_auth_url(app_key)}",
        )
    expires_at = (
        datetime.now(timezone.utc).astimezone()
        + timedelta(seconds=int(data.get("expires_in") or 0))
    ).strftime("%Y-%m-%d")
    store = _mcp_store()
    updated = []
    for name in _XIAODU_MCP_SERVERS:
        field = "env" if name == "xiaodu-iot" else "headers"
        store.update_server_config(name, {field: {"ACCESS_TOKEN": access}})
        updated.append(name)
    new_refresh = str(data.get("refresh_token") or refresh)
    save_config_value("smart_home_xiaodu_refresh_token", new_refresh)
    save_config_value("smart_home_xiaodu_token_expires_at", expires_at)
    return json.dumps({
        "success": True,
        "expires_at": expires_at,
        "servers_updated": updated,
        "refresh_token_rotated": new_refresh != refresh,
    }, ensure_ascii=False)
