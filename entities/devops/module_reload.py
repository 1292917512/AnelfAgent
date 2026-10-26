"""AI 运维热同步门面：复用实体/频道同步器，不持有额外运行态或回滚账本。

模型可见：目标模块、实际同步摘要和失败状态，仅作为工具结果返回。
Token 影响：按本次变动模块数增加结果，空闲时无开销。
缓存影响：结果仅进入工具链尾部；目录更新沿用注册表版本与追加式工具冻结。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from entities._sdk import (
    ErrorCause,
    channel_sensitive_allowed,
    error_from_exception,
    get_current_channel,
    sync_channel_modules,
    tool_error,
)

SyncTarget = Literal["entities", "channels", "all"]


def _result(action: str, results: dict[str, dict[str, Any]]) -> str:
    """保留各同步器事实；忙碌和部分失败不能报告为全部成功。"""
    busy = any(result.get("skipped") for result in results.values())
    failed = any(result.get("failed") or result.get("error") for result in results.values())
    if busy or failed:
        return tool_error(
            "模块同步未全部完成", cause=ErrorCause.STATE, retryable=False,
            hint="查看 results 中失败或跳过的目标；先核对运行状态，修复后按目标重试。已成功的改动不会整体撤回。",
            action=action, results=results,
        )
    return json.dumps({"ok": True, "action": action, "results": results}, ensure_ascii=False)


async def reload_entity_module(entity_name: str) -> str:
    """定向重载实体工具与路由入口，状态资源由原同步器保留。"""
    from entities import reload_entities

    name = entity_name.strip()
    if not name:
        return tool_error("entity_name 不能为空", cause=ErrorCause.PARAM, retryable=False)
    try:
        result = await reload_entities(reload_existing=True, entity_name=name)
        return _result("reload_entity", {"entities": result})
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.NOT_FOUND, retryable=False,
                          hint="使用 entities/ 下的实际模块目录名；核心框架与状态模块修改需重启应用。")
    except Exception as exc:
        return error_from_exception(exc, action=f"重载实体 {name}")


async def reload_channel_module(channel_id: str) -> str:
    """定向重载频道；敏感开关关闭时不触发同步。"""
    if not channel_sensitive_allowed():
        return tool_error("频道敏感操作已关闭", cause=ErrorCause.PERMISSION, retryable=False)
    cid = channel_id.strip()
    if not cid:
        return tool_error("channel_id 不能为空", cause=ErrorCause.PARAM, retryable=False)
    try:
        result = await sync_channel_modules(reload_existing=True, channel_id=cid)
        if cid == get_current_channel():
            result["warning"] = "本次重载涉及当前会话频道，连接可能短暂中断；请核对重连后的频道状态。"
        return _result("reload_channel", {"channels": result})
    except KeyError as exc:
        return tool_error(str(exc), cause=ErrorCause.NOT_FOUND, retryable=False)
    except Exception as exc:
        return error_from_exception(exc, action=f"重载频道 {cid}")


async def sync_module_directories(target: SyncTarget, reload_existing: bool) -> str:
    """批量对账前完成目标与权限预检；各同步器仍保有各自的单飞和回滚语义。"""
    from entities import reload_entities

    if target not in {"entities", "channels", "all"}:
        return tool_error("target 仅支持 entities、channels 或 all", cause=ErrorCause.PARAM, retryable=False)
    if target in {"channels", "all"} and not channel_sensitive_allowed():
        return tool_error("频道敏感操作已关闭，本次同步未执行", cause=ErrorCause.PERMISSION, retryable=False)
    results: dict[str, dict[str, Any]] = {}
    for kind in ("entities", "channels"):
        if target not in {kind, "all"}:
            continue
        try:
            results[kind] = await (reload_entities(reload_existing=reload_existing) if kind == "entities"
                                   else sync_channel_modules(reload_existing=reload_existing))
        except Exception as exc:
            results[kind] = json.loads(error_from_exception(exc, action=f"同步 {kind}"))
    return _result("sync_modules", results)
