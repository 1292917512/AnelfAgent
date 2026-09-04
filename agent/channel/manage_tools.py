"""频道启停管理工具 — AI 自主启停频道连接（与 Web 管理面同语义）。

启停意图持久化到 channels/<id>/channel_config.json 的 enabled 字段，重启后保持；
未注册的频道（启动期因 enabled=false 被跳过）由 ChannelManager.activate_channel
动态加载启动。

Model Experience 三行声明：
1. 模型看到什么：start_channel / stop_channel 两个工具（group=channel_ops，
   tags=["core"]，经实体目录/召回发现，非常驻注入）；list_channels 附带
   已配置但未启用的频道条目，供模型发现可启动目标。
2. token 影响：两个小型 schema，仅发现/调用时产生增量，量级可忽略。
3. 缓存影响：不动 stable/summary/conversation 层；tools 目录追加式冻结，不破前缀。

敏感操作：check_fn 复用 channel_tools_allow_sensitive 全局门控，
meta risk=CRITICAL 供审批规则引擎拦截。
"""

from __future__ import annotations

import asyncio
import json

from core.entity import EntityRegistry
from core.log import log
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_schema import extract_tool_params, get_first_line

from .tool_bridge import _sensitive_check

_START_TIMEOUT = 20.0
_STOP_TIMEOUT = 10.0


async def start_channel(channel_id: str) -> str:
    """启动指定频道并持久化为启用状态（重启后自动加载）；未注册的频道会动态加载启动。

    Args:
        channel_id: 频道标识（通过 list_channels 获取，含已配置未启用的频道）
    """
    from .channel_types import ChannelStatus
    from .manager import get_channel_manager, list_configured_channels, set_channel_enabled

    cid = (channel_id or "").strip()
    if not cid:
        return tool_error("channel_id 参数不能为空", cause=ErrorCause.PARAM,
                          retryable=False, hint="可用 list_channels 查看频道标识")
    cm = get_channel_manager()
    channel = cm.get(cid)
    if channel is not None and channel.status == ChannelStatus.RUNNING:
        return json.dumps({
            "success": True, "channel_id": cid, "status": ChannelStatus.RUNNING.value,
            "message": f"频道 '{cid}' 已在运行中",
        }, ensure_ascii=False)
    if channel is None and cid not in list_configured_channels():
        return tool_error(f"频道 '{cid}' 不存在", cause=ErrorCause.NOT_FOUND,
                          retryable=False,
                          hint="可用 list_channels 查看已注册与已配置的频道")
    try:
        set_channel_enabled(cid, True)
        if channel is not None:
            ok = await asyncio.wait_for(cm.start_channel(cid), timeout=_START_TIMEOUT)
        else:
            ok = await asyncio.wait_for(cm.activate_channel(cid), timeout=_START_TIMEOUT)
    except asyncio.TimeoutError:
        return tool_error(f"启动频道超时: {cid}", cause=ErrorCause.TIMEOUT, retryable=True)
    except Exception as exc:
        return error_from_exception(exc, action=f"启动频道 {cid}")
    if not ok:
        return tool_error(f"频道 '{cid}' 启动失败，详情见运行日志",
                          cause=ErrorCause.STATE, retryable=True)
    log(f"频道经 AI 工具启动: {cid}", tag="通道")
    return json.dumps({
        "success": True, "channel_id": cid, "status": ChannelStatus.RUNNING.value,
        "message": f"频道 '{cid}' 已启动并标记为启用（重启后自动加载）",
    }, ensure_ascii=False)


async def stop_channel(channel_id: str) -> str:
    """停止指定频道的连接并持久化为停用状态（重启后不再自动加载）。

    Args:
        channel_id: 频道标识（通过 list_channels 获取）
    """
    from .channel_types import ChannelStatus
    from .context import get_current_channel
    from .manager import get_channel_manager, list_configured_channels, set_channel_enabled

    cid = (channel_id or "").strip()
    if not cid:
        return tool_error("channel_id 参数不能为空", cause=ErrorCause.PARAM,
                          retryable=False, hint="可用 list_channels 查看频道标识")
    cm = get_channel_manager()
    channel = cm.get(cid)
    if channel is None:
        if cid not in list_configured_channels():
            return tool_error(f"频道 '{cid}' 不存在", cause=ErrorCause.NOT_FOUND,
                              retryable=False,
                              hint="可用 list_channels 查看已注册与已配置的频道")
        set_channel_enabled(cid, False)
        return json.dumps({
            "success": True, "channel_id": cid, "status": ChannelStatus.STOPPED.value,
            "message": f"频道 '{cid}' 未在运行，已标记为停用（重启后不再自动加载）",
        }, ensure_ascii=False)
    if channel.status == ChannelStatus.STOPPED:
        set_channel_enabled(cid, False)
        return json.dumps({
            "success": True, "channel_id": cid, "status": ChannelStatus.STOPPED.value,
            "message": f"频道 '{cid}' 已处于停止状态",
        }, ensure_ascii=False)
    try:
        ok = await asyncio.wait_for(cm.stop_channel(cid), timeout=_STOP_TIMEOUT)
    except asyncio.TimeoutError:
        return tool_error(f"停止频道超时: {cid}", cause=ErrorCause.TIMEOUT, retryable=True)
    except Exception as exc:
        return error_from_exception(exc, action=f"停止频道 {cid}")
    if not ok:
        return tool_error(f"频道 '{cid}' 停止失败，详情见运行日志",
                          cause=ErrorCause.STATE, retryable=True)
    set_channel_enabled(cid, False)
    log(f"频道经 AI 工具停止: {cid}", tag="通道")
    result = {
        "success": True, "channel_id": cid, "status": ChannelStatus.STOPPED.value,
        "message": f"频道 '{cid}' 已停止并标记为停用（重启后不再自动加载）",
    }
    if cid == get_current_channel():
        result["warning"] = "当前会话正通过该频道连接，停止后本次回复可能无法送达"
    return json.dumps(result, ensure_ascii=False)


def _register_manage_tools() -> None:
    """注册频道启停管理工具（幂等，重名时 register_tool 返回 False）。"""
    EntityRegistry.register_group(
        "channel_ops", "频道操作 — 频道能力接口（消息/群管等）与频道连接启停管理",
    )
    for fn, timeout in ((start_channel, _START_TIMEOUT + 15), (stop_channel, _STOP_TIMEOUT + 15)):
        EntityRegistry.register_tool(
            name=fn.__name__,
            func=fn,
            description=get_first_line(fn.__doc__) or fn.__name__,
            group="channel_ops",
            params=extract_tool_params(fn),
            tags=["core"],
            source="channel.manage",
            check_fn=_sensitive_check,
            meta={"risk": "CRITICAL", "timeout": timeout},
        )


_register_manage_tools()
