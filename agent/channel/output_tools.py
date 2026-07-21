"""统一输出工具 -- AI 通过这些工具向任意频道发送消息和媒体。

所有发送操作通过 ChannelManager 路由到具体频道实例。
发送成功后 AI 的回复会以 assistant 角色写入对话历史（主流做法），
使 AI 在后续对话中能看到自己说过什么，避免重复回复和上下文断裂。

频道能力自动注册：当频道连接后调用 register_channel_capability_tools()，
根据频道声明的 capabilities 和 BaseChannel 方法签名自动生成工具，
tag 标记为 capability 值 + channel_id，由 PFC tag 系统按需注入。
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable, Optional

from entities._sdk import deferred_tool, activate_group
from core.log import log

# 已由手动 @deferred_tool 注册的 capability（避免重复注册）
_MANUAL_CAPABILITIES = {"send_text", "send_photo", "send_voice", "send_file"}
# 不适合暴露为 LLM 工具的 capability
_SKIP_CAPABILITIES = {"streaming", "inline_keyboard", "reply_to"}

# 会话记录引用（register_output_tools 注入，用于将 AI 回复写入对话历史）
_conversation_data: Optional[Any] = None


def register_output_tools(conversation_data: Optional[Any] = None) -> None:
    """批量注册输出工具。"""
    global _conversation_data
    _conversation_data = conversation_data
    count = activate_group("output", "消息输出 — 向频道发送文本、图片、语音、文件等")
    log(f"统一输出工具已注册 ({count} 个)", tag="通道")


async def _record_sent_reply(target_id: str, content: str, channel_type: str) -> None:
    """将 AI 发送的回复记录到对话历史（assistant 角色）。

    主流做法：对话历史应同时包含用户消息与 AI 回复，
    否则 AI 在历史中看不到自己说过什么，导致重复回复/上下文断裂。
    """
    if _conversation_data is None or not content:
        return
    try:
        from agent.storage.storage_router import StorageDomain
        scope_type = "group" if channel_type == "group" else "user"
        await _conversation_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type=scope_type, scope_id=str(target_id),
            role="assistant", content=content,
        )
    except Exception as exc:
        log(f"回复记录失败: {exc}", "DEBUG", tag="通道")


def register_channel_capability_tools() -> int:
    """根据已连接频道的 capabilities 自动注册频道操作工具。

    扫描所有已注册频道，对每个 capability：
    - 跳过已手动注册的（send_text 等）和不适合暴露的（streaming 等）
    - 从 BaseChannel 方法签名自动提取参数
    - 注册为 channel_ops 分组工具，tag 包含 capability 值和所有支持该能力的频道 ID
    """
    from .channel import BaseChannel, ChannelCapability
    from .manager import get_channel_manager
    from core.entity import EntityRegistry

    cm = get_channel_manager()
    channels = cm.list_channels()
    if not channels:
        return 0

    # 收集每个 capability 被哪些频道支持
    cap_channels: dict[str, list[str]] = {}
    for cid, ch in channels.items():
        for cap in ch.capabilities:
            if cap.value in _MANUAL_CAPABILITIES or cap.value in _SKIP_CAPABILITIES:
                continue
            cap_channels.setdefault(cap.value, []).append(cid)

    from core.tags import tag_list, Tag as TagModel

    existing_tag_names = {t.tag_name for t in tag_list}

    registered = 0
    for cap_value, channel_ids in cap_channels.items():
        if cap_value in EntityRegistry.get_all_names():
            continue

        method = getattr(BaseChannel, cap_value, None)
        if method is None or not callable(method):
            continue

        if cap_value not in existing_tag_names:
            TagModel(tag_name=cap_value, tag_name_desc=f"频道能力: {cap_value}", visible_to_llm=False)
            existing_tag_names.add(cap_value)

        tags = [cap_value]
        tool_func = _make_capability_tool(cap_value, method)
        if tool_func:
            from core.entity import EntityMetadata, EntityType, ToolParam
            params = _extract_tool_params(method)
            EntityRegistry.register(EntityMetadata(
                name=cap_value,
                entity_type=EntityType.TOOL,
                description=_extract_description(method, cap_value),
                group="channel_ops",
                tags=tags,
                source="channel.auto",
                enabled=True,
                func=tool_func,
                is_async=True,
                meta={"params": params},
            ))
            registered += 1

    if registered:
        log(f"频道能力工具自动注册: {registered} 个", tag="通道")
    return registered


def _make_capability_tool(cap_value: str, method: Any):
    """为指定 capability 创建通用路由工具函数。"""
    sig = inspect.signature(method)
    param_names = [
        p.name for p in sig.parameters.values()
        if p.name not in ("self", "kwargs")
    ]

    async def _tool_func(channel_id: str = "", **kwargs: Any) -> str:
        ch, err = _validate_channel(channel_id)
        if err:
            return err
        fn = getattr(ch, cap_value, None)
        if fn is None:
            return json.dumps({"success": False, "error": f"频道 '{channel_id}' 不支持 {cap_value}"}, ensure_ascii=False)
        try:
            call_args = {k: v for k, v in kwargs.items() if k in param_names and not k.startswith("_")}
            if "chat_id" in call_args and isinstance(call_args["chat_id"], str):
                resolved_chat_id, forced_ct = _resolve_send_target(channel_id, call_args["chat_id"])
                call_args["chat_id"] = resolved_chat_id
                if forced_ct and "channel_type" in param_names and not call_args.get("channel_type"):
                    call_args["channel_type"] = forced_ct
            raw = await fn(**call_args)
            target_id = kwargs.get("chat_id", call_args.get("chat_id", ""))
            parsed, ok = _check_send_result(raw, channel_id, str(target_id))
            resolved_chat_id = call_args.get("chat_id")
            if isinstance(target_id, str) and isinstance(resolved_chat_id, str):
                _attach_target_resolution_meta(
                    parsed,
                    original_target_id=target_id,
                    resolved_target_id=resolved_chat_id,
                    resolved_channel_type=_resolve_channel_type(channel_id, resolved_chat_id),
                )
            if not ok:
                log(f"{cap_value} 失败: [{channel_id}] {parsed.get('error', '?')}", "WARNING", tag="通道")
            return json.dumps(parsed, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"success": False, "error": f"{cap_value} 执行失败: {exc}", "channel_id": channel_id}, ensure_ascii=False)

    # 复制方法签名中的参数注解给工具函数（EntityRegistry 靠这些生成 schema）
    new_params = [inspect.Parameter("channel_id", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                     default="", annotation=str)]
    for p in sig.parameters.values():
        if p.name in ("self", "kwargs"):
            continue
        new_params.append(p.replace(kind=inspect.Parameter.KEYWORD_ONLY))

    _tool_func.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
    _tool_func.__name__ = cap_value
    _tool_func.__doc__ = _extract_description(method, cap_value)
    return _tool_func


def _extract_tool_params(method: Any) -> list:
    """从方法签名提取 ToolParam 列表（含 channel_id 前缀），用于 schema 生成和 list_entity_methods。"""
    from core.entity import ToolParam
    from entities._sdk import _parse_docstring_args
    sig = inspect.signature(method)
    doc_params = _parse_docstring_args(getattr(method, "__doc__", "") or "")
    _TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}
    params: list[ToolParam] = [ToolParam(name="channel_id", type="string", required=False, description="频道标识")]
    for p in sig.parameters.values():
        if p.name in ("self", "kwargs"):
            continue
        json_type = _TYPE_MAP.get(p.annotation, "string") if p.annotation != inspect.Parameter.empty else "string"
        required = p.default is inspect.Parameter.empty
        params.append(ToolParam(
            name=p.name, type=json_type, required=required,
            description=doc_params.get(p.name, p.name),
        ))
    return params


def _extract_description(method: Any, fallback: str) -> str:
    """从方法 docstring 提取第一行作为工具描述。"""
    doc = getattr(method, "__doc__", "") or ""
    first_line = doc.strip().split("\n")[0].strip() if doc.strip() else ""
    return first_line or fallback.replace("_", " ").title()


def _get_channel(channel_id: str):
    from .manager import get_channel_manager
    return get_channel_manager().get(channel_id)


def _resolve_channel_type(channel_id: str, target_id: str) -> str:
    from .manager import get_channel_manager
    return get_channel_manager().resolve_channel_type(channel_id, target_id)


def _normalize_target_id(target_id: str) -> tuple[str, Optional[str]]:
    """标准化目标会话 ID，兼容 user:/group: 前缀写法。

    LLM 可能将纯数字 ID 按 JSON number 传递，此处统一转 str 容错。
    """
    raw = (str(target_id) if target_id is not None else "").strip()
    if not raw or ":" not in raw:
        return raw, None

    prefix, rest = raw.split(":", 1)
    value = rest.strip()
    if not value:
        return raw, None

    p = prefix.strip().lower()
    if p in {"user", "uid", "private", "friend", "dm"}:
        return value, "private"
    if p in {"group", "gid"}:
        return value, "group"
    return raw, None


def _resolve_send_target(channel_id: str, target_id: str) -> tuple[str, str]:
    """统一解析发送目标 ID 与 channel_type。"""
    resolved_target_id, forced_ct = _normalize_target_id(target_id)
    final_target_id = resolved_target_id or (str(target_id) if target_id is not None else "").strip()
    channel_type = forced_ct or _resolve_channel_type(channel_id, final_target_id)
    return final_target_id, channel_type


def _attach_target_resolution_meta(
        parsed: dict,
        *,
        original_target_id: str,
        resolved_target_id: str,
        resolved_channel_type: str,
) -> None:
    """在返回结果中附加目标规范化信息（仅当发生转换时）。"""
    if resolved_target_id != original_target_id:
        parsed["resolved_target_id"] = resolved_target_id
        parsed["resolved_channel_type"] = resolved_channel_type


def _list_running_channels() -> list[str]:
    """列出所有状态为 RUNNING 的频道 ID。"""
    from .channel import ChannelStatus
    from .manager import get_channel_manager
    return [
        cid for cid, ch in get_channel_manager().list_channels().items()
        if ch.status == ChannelStatus.RUNNING
    ]


def _validate_channel(channel_id: str) -> tuple[Any, Optional[str]]:
    """验证频道可用性。返回 (channel, error_json)。error_json 非 None 时表示不可用。"""
    from .channel import ChannelStatus

    if not channel_id:
        return None, json.dumps({
            "success": False,
            "error": "channel_id 参数不能为空",
            "available_channels": _list_running_channels(),
            "hint": "请使用 list_channels 获取可用频道",
        }, ensure_ascii=False)

    ch = _get_channel(channel_id)
    if not ch:
        return None, json.dumps({
            "success": False,
            "error": f"频道 '{channel_id}' 不存在",
            "available_channels": _list_running_channels(),
            "hint": "请使用 list_channels 获取可用频道",
        }, ensure_ascii=False)

    if ch.status != ChannelStatus.RUNNING:
        return None, json.dumps({
            "success": False,
            "error": f"频道 '{channel_id}' 未就绪（当前状态: {ch.status.value}）",
            "hint": "频道未启动或连接已断开",
        }, ensure_ascii=False)

    return ch, None


_NETWORK_UNAVAILABLE_KEYWORDS = (
    "connecterror", "networkerror", "connectionerror",
    "网络连接失败", "频道服务不可达",
    "connection refused", "connect call failed",
    "all connection attempts failed",
)


def _is_network_unavailable(error_msg: str) -> bool:
    lower = error_msg.lower()
    return any(k in lower for k in _NETWORK_UNAVAILABLE_KEYWORDS)


def _check_send_result(raw: Any, channel_id: str, target_id: str) -> tuple[dict, bool]:
    """解析频道发送结果。返回 (result_dict, is_success)。"""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        parsed = {"raw": str(raw)}
    if not isinstance(parsed, dict):
        parsed = {"raw": str(parsed)}
    parsed["channel_id"] = channel_id
    parsed["target_id"] = target_id
    ok = parsed.get("success") is not False
    if not ok and _is_network_unavailable(parsed.get("error", "")):
        parsed["retryable"] = False
        parsed["hint"] = "频道网络连接不可达，请勿重复发送，直接告知用户当前消息无法送达"
    return parsed, ok


async def _execute_send_action(
        *,
        channel_id: str,
        target_id: str,
        operation: str,
        invoke: Callable[[Any, str, str], Awaitable[Any]],
        enrich: Optional[Callable[[dict, bool], None]] = None,
        success_suffix: str = "",
) -> str:
    """统一发送执行管道：校验 -> 目标解析 -> 调用频道 -> 结果解析 -> 日志。"""
    ch, err = _validate_channel(channel_id)
    if err:
        return err

    try:
        resolved_target_id, channel_type = _resolve_send_target(channel_id, target_id)
        raw = await invoke(ch, resolved_target_id, channel_type)
        parsed, ok = _check_send_result(raw, channel_id, target_id)
        _attach_target_resolution_meta(
            parsed,
            original_target_id=target_id,
            resolved_target_id=resolved_target_id,
            resolved_channel_type=channel_type,
        )
        if enrich:
            enrich(parsed, ok)

        if ok:
            log(f"{operation}已发送: [{channel_id}] -> {target_id}{success_suffix}", tag="通道")
        else:
            log(f"{operation}发送失败: [{channel_id}] -> {target_id}: {parsed.get('error', '?')}", "WARNING", tag="通道")
        return json.dumps(parsed, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"发送{operation}失败: {e}",
            "channel_id": channel_id,
            "target_id": target_id,
        }, ensure_ascii=False)


# ── 工具实现 ─────────────────────────────────────────────────────────

@deferred_tool(group="output", tags=["core"], source="channel.output")
def list_channels() -> str:
    """列出所有已连接的通信频道及其能力和状态。"""
    try:
        from .manager import get_channel_manager
        cm = get_channel_manager()
        channels = cm.list_channels()
        if not channels:
            return json.dumps({"channels": [], "hint": "当前无已连接频道"}, ensure_ascii=False)
        result = []
        for cid, ch in channels.items():
            info = ch.get_status_info()
            result.append(info)
        return json.dumps({
            "channels": result,
            "total": len(result),
            "usage": "使用 send_message(channel_id, target_id, content) 发送消息",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@deferred_tool(group="output", tags=["always", "send_text"], source="channel.output")
async def send_message(
        channel_id: str,
        target_id: str,
        content: str = "",
        reply_to_message_id: str = "",
) -> str:
    """向指定频道发送文本消息。content 不能为空。

    在 content 中使用 [at_uid:用户uid] 格式可 @ 提及用户，
    uid 取自消息标签中的 [uid:xxx]。例如 [at_uid:12345]。
    @ 全体成员使用 [at_uid:all]。

    Args:
        channel_id: 频道标识（通过 list_channels 获取）
        target_id: 目标会话 ID（用户 uid 或群组 group_id，来自消息标签）
        content: 消息文本内容（支持 [at_uid:xxx] 格式 @ 提及用户）
        reply_to_message_id: 可选，指定回复引用的消息 ID（为空则普通发送）
    """
    if not content or not content.strip():
        return json.dumps({"success": False, "error": "content 参数不能为空，请提供要发送的消息内容"}, ensure_ascii=False)

    resolved_channel_type = "private"

    async def _invoke(ch: Any, resolved_target_id: str, channel_type: str) -> Any:
        nonlocal resolved_channel_type
        resolved_channel_type = channel_type
        log(f"调用 {channel_id}.send_text({resolved_target_id}, {channel_type}, {content[:50]}...)", "DEBUG", tag="通道")
        kwargs: dict[str, Any] = {"channel_type": channel_type}
        if reply_to_message_id:
            kwargs["reply_to"] = reply_to_message_id
        return await ch.send_text(resolved_target_id, content, **kwargs)

    def _enrich(parsed: dict, _: bool) -> None:
        parsed["content"] = content[:200]
        if reply_to_message_id:
            parsed["reply_to_message_id"] = reply_to_message_id

    result = await _execute_send_action(
        channel_id=channel_id,
        target_id=target_id,
        operation="消息",
        invoke=_invoke,
        enrich=_enrich,
        success_suffix=f" ({len(content)}字)",
    )

    # 发送成功后将 AI 回复记录到对话历史（assistant 角色）
    try:
        if json.loads(result).get("success") is not False:
            await _record_sent_reply(target_id, content, resolved_channel_type)
    except (json.JSONDecodeError, TypeError):
        pass
    return result


@deferred_tool(group="output", tags=["send_photo"], source="channel.output")
async def send_photo(channel_id: str, target_id: str, photo: str, caption: str = "") -> str:
    """向指定频道发送图片。

    Args:
        channel_id: 频道标识（通过 list_channels 获取）
        target_id: 目标会话 ID（用户 uid 或群组 group_id）
        photo: 图片文件路径或 URL
        caption: 图片说明文字
    """
    async def _invoke(ch: Any, resolved_target_id: str, channel_type: str) -> Any:
        return await ch.send_photo(resolved_target_id, photo, caption=caption, channel_type=channel_type)

    def _enrich(parsed: dict, ok: bool) -> None:
        parsed["media_path"] = photo
        if caption:
            parsed["caption"] = caption
        if ok:
            parsed["sent_media"] = f"[media_type:image][media_path:{photo}]"

    return await _execute_send_action(
        channel_id=channel_id,
        target_id=target_id,
        operation="图片",
        invoke=_invoke,
        enrich=_enrich,
    )


@deferred_tool(group="output", tags=["send_voice"], source="channel.output")
async def send_voice(channel_id: str, target_id: str, voice: str) -> str:
    """向指定频道发送语音消息。

    Args:
        channel_id: 频道标识（通过 list_channels 获取）
        target_id: 目标会话 ID（用户 uid 或群组 group_id）
        voice: 语音文件路径
    """
    async def _invoke(ch: Any, resolved_target_id: str, channel_type: str) -> Any:
        return await ch.send_voice(resolved_target_id, voice, channel_type=channel_type)

    def _enrich(parsed: dict, ok: bool) -> None:
        parsed["media_path"] = voice
        if ok:
            parsed["sent_media"] = f"[media_type:voice][media_path:{voice}]"

    return await _execute_send_action(
        channel_id=channel_id,
        target_id=target_id,
        operation="语音",
        invoke=_invoke,
        enrich=_enrich,
    )


@deferred_tool(group="output", tags=["send_file"], source="channel.output")
async def send_file(channel_id: str, target_id: str, file_path: str, caption: str = "") -> str:
    """向指定频道发送文件。

    Args:
        channel_id: 频道标识（通过 list_channels 获取）
        target_id: 目标会话 ID（用户 uid 或群组 group_id）
        file_path: 文件路径
        caption: 文件说明文字
    """
    async def _invoke(ch: Any, resolved_target_id: str, channel_type: str) -> Any:
        return await ch.send_file(resolved_target_id, file_path, caption=caption, channel_type=channel_type)

    def _enrich(parsed: dict, ok: bool) -> None:
        parsed["media_path"] = file_path
        if caption:
            parsed["caption"] = caption
        if ok:
            parsed["sent_media"] = f"[media_type:file][media_path:{file_path}]"

    return await _execute_send_action(
        channel_id=channel_id,
        target_id=target_id,
        operation="文件",
        invoke=_invoke,
        enrich=_enrich,
    )
