"""频道工具桥接 — @channel_tool 装饰器 + 自动注册/注销 + 通用路由。

设计：适配器用 ``@channel_tool`` 标记希望暴露给 AI 的方法，
``ChannelManager.register()`` 时由 ``register_channel_tools()`` 扫描并注册：

- **通用能力工具**：方法名命中 ``ChannelCapability``（经 ``CAPABILITY_METHOD_ALIAS``
  别名映射）时，跨频道共享一个同名工具（如 ``delete_message``），
  tags=[capability 值]，handler 按 显式 channel_id 参数 > 当前会话 ContextVar >
  报错 的优先级路由到目标频道实例。
- **频道特有工具**：其余标记方法注册为 ``{channel_id}_{method}`` 命名，
  tags=[channel_id]，handler 为绑定方法（无 channel_id 参数）。

PFC 注入沿用现有 tag 机制：capability tag 命中通用工具，adapter_key tag
命中特有工具——QQ 会话看到 qq_* 系列，telegram 会话看到 telegram_* 系列。

敏感操作（``sensitive=True``）附加 check_fn 门控，由配置
``channel_tools_allow_sensitive``（默认 true）全局开关。
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config import get_config_bool, register_configs_safe
from core.entity import EntityRegistry
from core.log import log
from core.tool_schema import extract_tool_params, get_first_line

from .channel_types import ChannelCapability
from .context import get_current_channel

# capability 值 → 实际业务方法名（处理与 BaseChannel 协议方法同名冲突）
CAPABILITY_METHOD_ALIAS: Dict[str, str] = {
    "forward_message": "forward_msg",
}

# 已由手写 output 工具覆盖的能力（send_message / send_photo / send_voice / send_file）
_MANUAL_CAPABILITIES = {"send_text", "send_photo", "send_voice", "send_file"}
# 不适合暴露为 LLM 工具的能力
_SKIP_CAPABILITIES = {"streaming", "inline_keyboard", "reply_to"}

# 方法名 → capability 值（别名反查）
_CAP_BY_METHOD: Dict[str, str] = {
    CAPABILITY_METHOD_ALIAS.get(c.value, c.value): c.value for c in ChannelCapability
}

_CHANNEL_TOOL_ATTR = "_channel_tool_meta"


@dataclass
class ChannelToolMeta:
    """@channel_tool 标记的元数据。"""

    name: Optional[str] = None
    description: Optional[str] = None
    sensitive: bool = False
    extra_tags: List[str] = field(default_factory=list)


def channel_tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    sensitive: bool = False,
    extra_tags: Optional[List[str]] = None,
) -> Callable:
    """标记频道适配器方法为 AI 可见工具（仅打标记，注册在频道注册时发生）。

    Args:
        name: 工具名覆盖（特有工具仍会自动加 {channel_id}_ 前缀）
        description: 工具描述（缺省取 docstring 首行）
        sensitive: 是否敏感操作（受 channel_tools_allow_sensitive 配置门控）
        extra_tags: 附加 tag（channel_id / capability tag 自动添加）
    """
    def decorator(func: Callable) -> Callable:
        setattr(func, _CHANNEL_TOOL_ATTR, ChannelToolMeta(
            name=name, description=description,
            sensitive=sensitive, extra_tags=list(extra_tags or []),
        ))
        return func
    return decorator


# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------

_CHANNEL_TOOL_CONFIGS = {
    "频道工具": {
        "channel_tools_allow_sensitive": {
            "description": "是否向 AI 暴露敏感频道操作（踢人/退群/全体禁言/审批等）",
            "default": True,
        },
    },
}

register_configs_safe(_CHANNEL_TOOL_CONFIGS)


def _sensitive_check() -> bool:
    """敏感频道操作门控。"""
    return get_config_bool("channel_tools_allow_sensitive", True)


# ------------------------------------------------------------------
# 注册状态
# ------------------------------------------------------------------

# capability 值 -> {channel_id: (绑定方法, meta)}
_common_methods: Dict[str, Dict[str, Tuple[Callable, ChannelToolMeta]]] = {}
# channel_id -> 已注册的特有工具名
_specific_tools: Dict[str, List[str]] = {}


def register_channel_tools(channel: Any) -> int:
    """扫描频道上 @channel_tool 标记的方法并注册为 LLM 工具（幂等）。"""
    cid = channel.channel_id
    unregister_channel_tools(cid)

    marked = _collect_marked_methods(channel)
    if not marked:
        return 0

    registered = 0
    for method_name, (bound, meta) in marked.items():
        cap_value = _CAP_BY_METHOD.get(method_name)
        if cap_value is not None:
            # 能力方法：仅在频道声明该能力时进入通用工具，否则不暴露
            if (
                cap_value not in _MANUAL_CAPABILITIES
                and cap_value not in _SKIP_CAPABILITIES
                and _declares_capability(channel, cap_value)
            ):
                _common_methods.setdefault(cap_value, {})[cid] = (bound, meta)
                if _rebuild_common_tool(cap_value):
                    registered += 1
        elif _register_specific_tool(cid, bound, meta):
            registered += 1

    if registered:
        log(f"频道工具已注册: [{cid}] {registered} 个", tag="通道")
    return registered


def unregister_channel_tools(channel_id: str) -> None:
    """注销频道的特有工具，并重算其参与的通用工具。"""
    for tool_name in _specific_tools.pop(channel_id, []):
        EntityRegistry.unregister(tool_name)

    empty_caps: List[str] = []
    for cap_value, supporters in _common_methods.items():
        if channel_id in supporters:
            del supporters[channel_id]
            if supporters:
                _rebuild_common_tool(cap_value)
            else:
                empty_caps.append(cap_value)
    for cap_value in empty_caps:
        del _common_methods[cap_value]
        EntityRegistry.unregister(cap_value)


# ------------------------------------------------------------------
# 内部实现
# ------------------------------------------------------------------

def _collect_marked_methods(channel: Any) -> Dict[str, Tuple[Callable, ChannelToolMeta]]:
    """遍历类 MRO 收集 @channel_tool 标记的方法（子类覆盖优先）。"""
    marked: Dict[str, Tuple[Callable, ChannelToolMeta]] = {}
    for klass in type(channel).mro():
        for name, member in vars(klass).items():
            meta = getattr(member, _CHANNEL_TOOL_ATTR, None)
            if meta is None or name in marked:
                continue
            bound = getattr(channel, name, None)
            if callable(bound):
                marked[name] = (bound, meta)
    return marked


def _declares_capability(channel: Any, cap_value: str) -> bool:
    """频道是否声明了指定能力。"""
    try:
        return ChannelCapability(cap_value) in set(getattr(channel, "capabilities", set()) or set())
    except ValueError:
        return False


def _register_specific_tool(channel_id: str, bound: Callable, meta: ChannelToolMeta) -> bool:
    """注册频道特有工具：{channel_id}_{method}，tags=[channel_id]。"""
    base_name = meta.name or getattr(bound, "__name__", "tool")
    prefix = f"{channel_id}_"
    tool_name = base_name if base_name.startswith(prefix) else f"{prefix}{base_name}"

    description = meta.description or get_first_line(getattr(bound, "__doc__", None)) or tool_name
    tags = [channel_id, *meta.extra_tags]

    ok = EntityRegistry.register_tool(
        name=tool_name,
        func=_make_specific_handler(channel_id, tool_name, bound),
        description=description,
        group="channel_ops",
        params=extract_tool_params(bound),
        tags=tags,
        source=f"channel.{channel_id}",
        check_fn=_sensitive_check if meta.sensitive else None,
    )
    if ok:
        _specific_tools.setdefault(channel_id, []).append(tool_name)
    else:
        log(f"频道特有工具注册失败(重名): {tool_name}", "WARNING", tag="通道")
    return ok


def _make_specific_handler(channel_id: str, tool_name: str, bound: Callable) -> Callable:
    """特有工具薄封装：目标解析（chat_id/channel_type）+ 结果规范化。"""
    async def _handler(**kwargs: Any) -> str:
        try:
            raw = bound(**_prepare_call(bound, kwargs, channel_id))
            if inspect.isawaitable(raw):
                raw = await raw
            return _normalize_result(raw, tool_name, channel_id)
        except Exception as exc:
            return json.dumps({
                "success": False,
                "error": f"{tool_name} 执行失败: {exc}",
                "channel_id": channel_id,
            }, ensure_ascii=False)

    _handler.__name__ = tool_name
    return _handler


def _rebuild_common_tool(cap_value: str) -> bool:
    """按当前支持频道集合重建通用能力工具（合并 schema + 路由 handler）。"""
    supporters = _common_methods.get(cap_value)
    if not supporters:
        return False

    method_name = CAPABILITY_METHOD_ALIAS.get(cap_value, cap_value)
    params = _merge_params(cap_value, supporters)
    sensitive = any(meta.sensitive for _, meta in supporters.values())
    description = _common_description(cap_value, supporters)

    EntityRegistry.unregister(cap_value)
    return EntityRegistry.register_tool(
        name=cap_value,
        func=_make_common_handler(cap_value, method_name),
        description=description,
        group="channel_ops",
        params=params,
        tags=[cap_value],
        source="channel.auto",
        check_fn=_sensitive_check if sensitive else None,
    )


def _merge_params(cap_value: str, supporters: Dict[str, Tuple[Callable, ChannelToolMeta]]) -> list:
    """合并各频道实现的参数 schema：并集，required 取交集，前置 channel_id。"""
    from core.entity import ToolParam

    merged: Dict[str, ToolParam] = {}
    required_in_all: Optional[set] = None
    for cid in sorted(supporters):
        bound, _ = supporters[cid]
        params = extract_tool_params(bound)
        required_names = {p.name for p in params if p.required}
        required_in_all = required_names if required_in_all is None else (required_in_all & required_names)
        for p in params:
            if p.name not in merged:
                merged[p.name] = p

    result = [ToolParam(
        name="channel_id", type="string", required=False,
        description="目标频道标识（缺省取当前会话频道）",
    )]
    for p in merged.values():
        p.required = p.name in (required_in_all or set())
        result.append(p)
    return result


def _common_description(cap_value: str, supporters: Dict[str, Tuple[Callable, ChannelToolMeta]]) -> str:
    """取首个带 docstring 的实现首行作为工具描述。"""
    for cid in sorted(supporters):
        bound, meta = supporters[cid]
        desc = meta.description or get_first_line(getattr(bound, "__doc__", None))
        if desc:
            return desc
    return cap_value.replace("_", " ")


def _make_common_handler(cap_value: str, method_name: str) -> Callable:
    """创建通用能力工具 handler：路由到目标频道实例的同名业务方法。"""
    async def _handler(**kwargs: Any) -> str:
        from .manager import get_channel_manager

        channel_id = str(kwargs.pop("channel_id", "") or get_current_channel() or "")
        cm = get_channel_manager()
        ch = cm.get(channel_id) if channel_id else None
        if ch is None:
            return json.dumps({
                "success": False,
                "error": f"未确定目标频道（channel_id='{channel_id or '空'}'）",
                "supporting_channels": sorted(_common_methods.get(cap_value, {})),
                "hint": "请显式传入 channel_id（可用 list_channels 查看）",
            }, ensure_ascii=False)

        fn = getattr(ch, method_name, None)
        supporter = _common_methods.get(cap_value, {}).get(channel_id)
        if fn is None or supporter is None:
            return json.dumps({
                "success": False,
                "error": f"频道 '{channel_id}' 不支持 {cap_value}",
                "supporting_channels": sorted(_common_methods.get(cap_value, {})),
            }, ensure_ascii=False)

        fn = supporter[0]
        call_kwargs = _prepare_call(fn, kwargs, channel_id)
        try:
            raw = fn(**call_kwargs)
            if inspect.isawaitable(raw):
                raw = await raw
            return _normalize_result(raw, cap_value, channel_id)
        except Exception as exc:
            return json.dumps({
                "success": False,
                "error": f"{cap_value} 执行失败: {exc}",
                "channel_id": channel_id,
            }, ensure_ascii=False)

    _handler.__name__ = cap_value
    return _handler


def _prepare_call(fn: Callable, kwargs: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
    """调用前处理：chat_id 前缀解析 + channel_type 自动注入 + 签名过滤。"""
    from .output_tools import _resolve_send_target

    accepts_kwargs = _accepts_var_kwargs(fn)
    if "chat_id" in kwargs and isinstance(kwargs["chat_id"], str):
        resolved, channel_type = _resolve_send_target(channel_id, kwargs["chat_id"])
        kwargs["chat_id"] = resolved
        if "channel_type" not in kwargs and (accepts_kwargs or _has_param(fn, "channel_type")):
            kwargs["channel_type"] = channel_type
    if accepts_kwargs:
        return kwargs
    return _filter_kwargs(fn, kwargs)


def _accepts_var_kwargs(fn: Callable) -> bool:
    try:
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in inspect.signature(fn).parameters.values()
        )
    except (TypeError, ValueError):
        return False


def _has_param(fn: Callable, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _filter_kwargs(fn: Callable, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """按目标方法签名过滤参数（方法接受 **kwargs 时全透传）。"""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return kwargs
    params = sig.parameters.values()
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in sig.parameters}


def _normalize_result(raw: Any, cap_value: str, channel_id: str) -> str:
    """统一返回 JSON 字符串，并附带 channel_id。"""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    else:
        parsed = raw
    if isinstance(parsed, dict):
        parsed.setdefault("channel_id", channel_id)
        return json.dumps(parsed, ensure_ascii=False)
    if parsed is None:
        return json.dumps({"success": True, "channel_id": channel_id}, ensure_ascii=False)
    return json.dumps({"success": True, "result": parsed, "channel_id": channel_id}, ensure_ascii=False)
