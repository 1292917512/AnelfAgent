"""音源同步 AI 工具面：同步开关与状态（音频库管理工具在核心 audio 组）。

同步是隐私与资源敏感操作（批量转写 + 声纹识别），工具可被审批规则
引擎按组拦截；周期同步默认关闭（audiosync_watch_enabled）。
"""

from __future__ import annotations

import json
from typing import Any

from entities._sdk import ErrorCause, error_from_exception, tool, tool_error

_group = "audiosync"


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


@tool(group=_group, tags=["core"])
async def audiosync_service(action: str = "get", key: str = "", value: str = "") -> str:
    """查询或配置转写服务，保存后报告实际可达状态。

    Args:
        action: get（配置与可达状态）或 set（写入一项配置）。
        key: set 时填写 funasr_endpoint（HTTP 地址）或 funasr_timeout（秒）。
        value: 待保存的配置值。
    """
    from core.config import ConfigManager
    from core.provider_keys import set_provider_key
    from entities._sdk import save_config_value

    from . import client

    try:
        action = action.strip().lower()
        if action == "get":
            client.reset_probe_cache()
            return _dump({"success": True, "config": {
                "funasr_endpoint": client.endpoint_config(),
                "funasr_timeout": ConfigManager.get("funasr_timeout", 120),
                "funasr_reachable": await client.probe_available(),
            }})
        if action != "set":
            return tool_error("操作必须为 get 或 set", cause=ErrorCause.PARAM, retryable=False)
        if key == "funasr_timeout":
            from core.config import ConfigRegistry
            item = ConfigRegistry.get_item(key)
            if item is None:
                return tool_error("转写服务配置尚未注册", cause=ErrorCause.STATE, retryable=False)
            save_config_value(key, item.coerce_value(value))
            return _dump({"success": True, "key": key, "value": ConfigManager.get(key)})
        if key != "funasr_endpoint":
            return tool_error("未知服务配置项", cause=ErrorCause.PARAM, retryable=False,
                              hint="可选: funasr_endpoint / funasr_timeout")
        from urllib.parse import urlsplit
        endpoint = value.strip()
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return tool_error("服务地址必须是完整 HTTP(S) URL", cause=ErrorCause.PARAM, retryable=False)
        set_provider_key("funasr", key, endpoint)
        client.reset_probe_cache()
        reachable = await client.probe_available()
        return _dump({"success": True, "key": key, "value": endpoint, "reachable": reachable,
                      "hint": "服务在线" if reachable else "地址已保存但服务不可达，请检查服务进程与网络"})
    except Exception as exc:
        return error_from_exception(exc, action="转写服务配置")


@tool(group=_group, tags=["core"])
async def audiosync_sync_now() -> str:
    """触发一轮音源目录增量同步（后台执行）：扫描配置的音频来源（本地目录/OpenList），新增文件自动转写并声纹识别入库。

    同步是长批量操作，本工具立即返回不阻塞：completed=true 时 result 为本轮摘要；
    started=true 且 completed=false 表示仍在后台执行；started=false 表示已有一轮
    同步进行中（不会重复发起）。后两种情况经 audiosync_status 轮询进度与结果。
    """
    try:
        from .watcher import get_audiosync_watcher
        watcher = get_audiosync_watcher()
        result = await watcher.trigger()
        result["status"] = watcher.status()
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="音源目录同步")


@tool(group=_group, tags=["always"], concurrency_safe=True)
async def audiosync_status() -> str:
    """查看音源同步状态：来源组件/是否暂停/最近扫描结果/错误。"""
    try:
        from .watcher import get_audiosync_watcher
        return _dump(get_audiosync_watcher().status())
    except Exception as e:
        return error_from_exception(e, action="查看音源同步状态")


@tool(group=_group, tags=["core"])
async def audiosync_rebuild(paths: str) -> str:
    """删除重建指定录制：清理本地资源（片段/样本）并立即从来源重新入库（支持批量）。

    Args:
        paths: 录制单元路径（文件夹或文件），多个用逗号分隔；
               来源上已不存在的仅做本地清理；命中排除规则的路径拒绝重建
    """
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    if not path_list:
        return tool_error("paths 不能为空", cause=ErrorCause.PARAM, retryable=False,
                          hint="传入录制文件夹/文件路径，如 /个人数据/音源/audio_20260806182341")
    try:
        from .watcher import get_audiosync_watcher
        result = await get_audiosync_watcher().rebuild(path_list[:50])
        return _dump(result)
    except Exception as e:
        return error_from_exception(e, action="删除重建录制")
