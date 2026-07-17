"""Responses 请求路由与能力校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from agent.llm.protocol import (
    BUILTIN_TOOL_TYPES,
    ProviderCapability,
    TransportMode,
    get_provider_capability,
    is_native_responses_provider,
)


class ResponsesCapabilityError(ValueError):
    """请求的 Responses 能力不受当前 provider 支持。"""


@dataclass(frozen=True, slots=True)
class ResponsesRoute:
    """一次 Responses 调用的路由决策。"""

    transport: TransportMode
    capability: ProviderCapability
    force_chat_completions_api: bool
    api_type: str
    api_base: str


_OFFICIAL_OPENAI_HOSTS = frozenset({
    "api.openai.com",
    "openai.azure.com",
})


def _host_of(api_base: str) -> str:
    if not api_base:
        return ""
    parsed = urlparse(api_base if "://" in api_base else f"https://{api_base}")
    return (parsed.hostname or "").lower()


def is_custom_openai_compatible(api_type: str, api_base: str) -> bool:
    """openai/azure 但指向自定义兼容端点。"""
    if api_type not in {"openai", "azure"}:
        return False
    host = _host_of(api_base)
    if not host:
        return api_type == "openai"
    if host in _OFFICIAL_OPENAI_HOSTS:
        return False
    if host.endswith(".openai.azure.com") or host.endswith(".azure.com"):
        return False
    return True


def resolve_responses_route(
    *,
    api_type: str,
    api_base: str = "",
    prefer_bridge_for_custom: bool = True,
) -> ResponsesRoute:
    """按 provider/base_url 选择 native 或 bridge。"""
    capability = get_provider_capability(api_type)
    if capability.create == TransportMode.UNSUPPORTED:
        return ResponsesRoute(
            transport=TransportMode.UNSUPPORTED,
            capability=capability,
            force_chat_completions_api=False,
            api_type=api_type,
            api_base=api_base,
        )

    if is_native_responses_provider(api_type):
        if prefer_bridge_for_custom and is_custom_openai_compatible(api_type, api_base):
            # 兼容网关未必实现 /responses，默认走 bridge，避免直接 404。
            return ResponsesRoute(
                transport=TransportMode.BRIDGE,
                capability=capability,
                force_chat_completions_api=True,
                api_type=api_type,
                api_base=api_base,
            )
        return ResponsesRoute(
            transport=TransportMode.NATIVE,
            capability=capability,
            force_chat_completions_api=False,
            api_type=api_type,
            api_base=api_base,
        )

    return ResponsesRoute(
        transport=TransportMode.BRIDGE,
        capability=capability,
        force_chat_completions_api=True,
        api_type=api_type,
        api_base=api_base,
    )


def require_operation(route: ResponsesRoute, operation: str) -> None:
    """校验操作是否可用。"""
    mode = getattr(route.capability, operation, TransportMode.UNSUPPORTED)
    if mode == TransportMode.UNSUPPORTED:
        raise ResponsesCapabilityError(
            f"当前 provider ({route.api_type}) 不支持 Responses.{operation}"
        )
    if operation in {"retrieve", "delete", "cancel", "compact", "previous_response_id"}:
        if route.transport != TransportMode.NATIVE:
            raise ResponsesCapabilityError(
                f"Responses.{operation} 仅支持 native OpenAI/Azure 会话"
            )


def validate_tools_for_route(
    route: ResponsesRoute,
    tools: Optional[Iterable[Any]],
) -> None:
    """校验工具类型是否被允许。"""
    if not tools:
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type", "function"))
        if tool_type in {"function", "custom"}:
            continue
        if tool_type in BUILTIN_TOOL_TYPES:
            if route.capability.builtin_tools == TransportMode.UNSUPPORTED:
                raise ResponsesCapabilityError(
                    f"当前 provider 不支持内置工具: {tool_type}"
                )
            if route.transport != TransportMode.NATIVE:
                raise ResponsesCapabilityError(
                    f"内置工具 {tool_type} 仅支持 native Responses 路径"
                )
            continue
        raise ResponsesCapabilityError(f"不支持的工具类型: {tool_type}")
