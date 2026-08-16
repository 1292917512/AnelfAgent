"""NoneBot 桥接 AI 工具 — Web 与 AI 双通道完全自主管理的工具面。

经 ``register_nonebot_tools()`` 模块级注册到 EntityRegistry（group=nonebot），
不依赖桥接频道是否启用：环境引导 / 适配器装卸 / 插件装卸 / 配置读写
在频道未启用时同样可用（安装落在 worker venv，启用频道后即生效）。

注册时机：``web/routers/nonebot.py`` 导入时调用（Web 服务随应用启动，
单元测试不导入该路由，避免污染全局注册表）。

敏感操作（装卸/升级/重建/配置写入/生命周期）附加 check_fn 门控，
由 ``channel_tools_allow_sensitive`` 全局开关控制。
"""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from agent.channel.channel_types import _err, _ok
from core.entity import EntityRegistry
from core.log import log
from core.tool_schema import extract_tool_params

_TOOLS_REGISTERED = False

_ADAPTER_ACTIONS = ("install", "uninstall", "enable", "disable")
_PLUGIN_ACTIONS = ("install", "uninstall", "enable", "disable")
_ENV_ACTIONS = ("bootstrap", "upgrade", "rebuild", "resync")
_LIFECYCLE_ACTIONS = ("start", "stop", "restart")


def _sensitive_ok() -> bool:
    """敏感操作门控（与频道工具同规则）。"""
    from core.config import get_config_bool

    return get_config_bool("channel_tools_allow_sensitive", True)


def _register(name: str, func: Callable[..., Any], description: str, sensitive: bool = False) -> None:
    """注册单个工具（幂等由调用方保证）。"""
    ok = EntityRegistry.register_tool(
        name=name,
        func=func,
        description=description,
        group="nonebot",
        params=extract_tool_params(func),
        tags=["always"],
        source="channel.nonebot_bridge",
        check_fn=_sensitive_ok if sensitive else None,
        meta={"risk": "CRITICAL"} if sensitive else None,
    )
    if not ok:
        log(f"NoneBot 工具注册失败(重名): {name}", "WARNING", tag="通道")


def _service() -> Any:
    from services.nonebot import NoneBotService

    return NoneBotService()


def _result(payload: Any) -> str:
    """把服务层 dict 结果序列化为工具返回 JSON。"""
    if isinstance(payload, dict):
        return _ok(payload) if payload.get("success", True) else _err(
            str(payload.get("error") or "操作失败")
        )
    return _ok({"result": payload})


# ------------------------------------------------------------------
# 状态与诊断
# ------------------------------------------------------------------


async def _tool_status() -> str:
    """全景状态：环境 / worker 进程 / 在线 Bot / 已启用适配器 / 已加载插件。"""
    status = await _service().get_status()
    channel_status = status.get("channel_status") or {}
    snapshot = channel_status.get("worker_snapshot") or {}
    status["bots"] = snapshot.get("bots", [])
    status["loaded_adapters"] = snapshot.get("adapters", [])
    status["loaded_plugins"] = [
        p.get("module", "") for p in snapshot.get("plugins", []) if isinstance(p, dict)
    ]
    status.pop("channel_status", None)  # 冗余大对象裁剪
    return _result(status)


async def _tool_env_status() -> str:
    """worker 环境详情：uv / Python 版本、venv 就绪态、基线包、安装进度。"""
    return _result(await _service().get_env_status())


async def _tool_env_packages() -> str:
    """列出 worker venv 已安装的包（名称 + 版本）。"""
    return _result(await _service().list_packages())


async def _tool_logs(count: int = 80) -> str:
    """读取 worker 日志尾部（排障用）。"""
    return _result({"logs": _service().tail_logs(max(1, min(count, 500)))})


# ------------------------------------------------------------------
# 环境管理（敏感）
# ------------------------------------------------------------------


async def _tool_env_manage(action: str, packages: Optional[List[str]] = None) -> str:
    """环境管理：bootstrap 初始化 venv / upgrade 升级包(缺省 NoneBot 基线) / rebuild 重建。"""
    if action not in _ENV_ACTIONS:
        return _err(f"未知 action '{action}'，支持: {list(_ENV_ACTIONS)}")
    svc = _service()
    if action == "bootstrap":
        return _result(await svc.bootstrap_env())
    if action == "upgrade":
        return _result(await svc.upgrade_env(packages or None))
    if action == "resync":
        return _result(await svc.resync_sources())
    return _result(await svc.rebuild_env())


# ------------------------------------------------------------------
# 适配器 / 插件管理（敏感）
# ------------------------------------------------------------------


async def _tool_manage_adapter(
    action: str, key: str, source: str = ""
) -> str:
    """适配器管理：install 安装（source 可填 git 源如 git+https://x.git 或本地路径，空=商店）/ uninstall / enable / disable。"""
    if action not in _ADAPTER_ACTIONS:
        return _err(f"未知 action '{action}'，支持: {list(_ADAPTER_ACTIONS)}")
    svc = _service()
    if action == "install":
        return _result(await svc.install_adapter(key, enable=True, source=source))
    if action == "uninstall":
        return _result(await svc.uninstall_adapter(key))
    return _result(svc.set_adapter_enabled(key, action == "enable"))


async def _tool_manage_plugin(
    action: str, module: str, source: str = "", editable: bool = False
) -> str:
    """插件管理：install（source 可填 git 源/本地路径，editable=本地可编辑安装）/ uninstall / enable / disable。"""
    if action not in _PLUGIN_ACTIONS:
        return _err(f"未知 action '{action}'，支持: {list(_PLUGIN_ACTIONS)}")
    svc = _service()
    if action == "install":
        return _result(await svc.install_plugin(module, source=source, editable=editable))
    if action == "uninstall":
        return _result(await svc.uninstall_plugin(module))
    return _result(svc.set_plugin_enabled(module, action == "enable"))


# ------------------------------------------------------------------
# 配置读写
# ------------------------------------------------------------------


async def _tool_config_get() -> str:
    """读取桥接配置（敏感环境变量已遮盖）：adapters/plugins/nonebot_env/端口/intercept_all 等。"""
    cfg = _service().get_config_masked()
    return _result({"config": cfg})


async def _tool_config_set(key: str, value: str) -> str:
    """写入配置项（敏感）：顶层项如 intercept_all/worker_port，或 nonebot_env.<ENV_KEY>（空值删除）。"""
    parsed: Any = value
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            pass
    return _result(_service().set_config_value(key, parsed))


# ------------------------------------------------------------------
# 生命周期（敏感）
# ------------------------------------------------------------------


async def _tool_lifecycle(action: str) -> str:
    """worker 生命周期：start 启动 / stop 停止 / restart 重启（频道须已启用）。"""
    if action not in _LIFECYCLE_ACTIONS:
        return _err(f"未知 action '{action}'，支持: {list(_LIFECYCLE_ACTIONS)}")
    svc = _service()
    if action == "start":
        return _result(await svc.start_worker())
    if action == "stop":
        return _result(await svc.stop_worker())
    return _result(await svc.restart())


# ------------------------------------------------------------------
# 商店 / 命令 / 发送
# ------------------------------------------------------------------


async def _tool_store_search(keyword: str, limit: int = 8) -> str:
    """搜索 NoneBot 插件商店（名称/描述/作者/标签），返回 module_name 供安装使用。"""
    results = await _service().search_store_plugins(keyword, limit=max(1, min(limit, 30)))
    if not results:
        return _ok({"keyword": keyword, "results": [], "hint": "未找到匹配插件"})
    return _ok({"keyword": keyword, "count": len(results), "results": results})


async def _tool_run_command(command: str, bot_id: str = "", adapter: str = "") -> str:
    """以虚拟用户身份触发 NoneBot 插件命令并捕获回复（如 /help，不发送到平台）。"""
    result = await _service().run_command(command, bot_id=bot_id, adapter=adapter)
    if not result.get("ok"):
        return _err(str(result.get("error") or "命令执行失败"))
    return _ok({
        "command": command,
        "replies": result.get("replies", []),
        "timeout": bool(result.get("timeout", False)),
    })


async def _tool_send(
    chat_id: str,
    text: str = "",
    image: str = "",
    voice: str = "",
    video: str = "",
    file: str = "",
    channel_type: str = "private",
    bot_id: str = "",
    adapter: str = "",
) -> str:
    """经桥接向平台目标发送消息（text/image/voice/video/file 至少一项，媒体为本地路径或 URL）。"""
    media = [
        (kind, source)
        for kind, source in (("image", image), ("voice", voice), ("video", video), ("file", file))
        if source.strip()
    ]
    if not text and not media:
        return _err("text/image/voice/video/file 至少提供一项")
    if len(media) > 1:
        return _err("一次只支持一种媒体（image/voice/video/file 单选）")

    svc = _service()
    if media:
        kind, source = media[0]
        channel_result = await svc.send_media_to_platform(
            chat_id, kind, source, caption=text,
            channel_type=channel_type, bot_id=bot_id, adapter=adapter,
        )
    else:
        channel_result = await svc.send_to_platform(
            chat_id, text, channel_type=channel_type, bot_id=bot_id, adapter=adapter,
        )
    return _result(channel_result)


# ------------------------------------------------------------------
# 注册入口
# ------------------------------------------------------------------

_TOOL_SPECS: List[tuple] = [
    ("nonebot_status", _tool_status, "查询 NoneBot 桥接全景状态：环境 / worker 进程 / 在线 Bot / 适配器 / 插件", False),
    ("nonebot_env_status", _tool_env_status, "查询 worker 环境详情：uv / Python 版本、venv 就绪态、基线包", False),
    ("nonebot_env_packages", _tool_env_packages, "列出 worker venv 已安装的包（名称 + 版本）", False),
    ("nonebot_env_manage", _tool_env_manage, "环境管理：bootstrap 初始化 / upgrade 升级(缺省 NoneBot 基线) / rebuild 重建 venv", True),
    ("nonebot_manage_adapter", _tool_manage_adapter, "适配器管理：install / uninstall / enable / disable（key 如 onebot_v11；install 可指定 git 源或本地路径）", True),
    ("nonebot_manage_plugin", _tool_manage_plugin, "插件管理：install（商店 / git 源 / 本地路径可编辑）/ uninstall / enable / disable（module 为插件模块名）", True),
    ("nonebot_config_get", _tool_config_get, "读取桥接配置（敏感值已遮盖）：adapters / plugins / nonebot_env / 端口等", False),
    ("nonebot_config_set", _tool_config_set, "写入配置项：顶层项（intercept_all/worker_port 等）或 nonebot_env.<ENV_KEY>（空值删除）", True),
    ("nonebot_lifecycle", _tool_lifecycle, "worker 生命周期：start / stop / restart（频道须已启用）", True),
    ("nonebot_logs", _tool_logs, "读取 worker 日志尾部（排障）", False),
    ("nonebot_store_search", _tool_store_search, "搜索 NoneBot 插件商店，返回 module_name 供安装", False),
    ("nonebot_run_command", _tool_run_command, "以虚拟用户触发插件命令并捕获回复（如 /help）", False),
    ("nonebot_send", _tool_send, "经桥接向平台目标发送消息（文本/图片/语音/视频/文件，可指定 bot_id / adapter）", False),
]


def register_nonebot_tools() -> int:
    """注册全部 NoneBot 工具（幂等），返回注册数量。"""
    global _TOOLS_REGISTERED
    if _TOOLS_REGISTERED:
        return 0
    count = 0
    for name, func, description, sensitive in _TOOL_SPECS:
        _register(name, func, description, sensitive)
        count += 1
    _TOOLS_REGISTERED = True
    log(f"NoneBot AI 工具已注册: {count} 个", tag="通道")
    return count
