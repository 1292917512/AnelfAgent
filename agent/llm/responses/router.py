"""Responses 请求路由与能力校验。

路由语义：openai/azure 一律 native 直连 /responses（显式选择 responses 协议
即绝对走官方接口；auto 模式下端点未实现 /responses 的回退由 LLMClient 处理），
其余 api_type（anthropic 等）没有官方 Responses 端点，经 litellm bridge 桥接。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from agent.llm.protocol import (
    BUILTIN_TOOL_TYPES,
    ProviderCapability,
    TransportMode,
    get_provider_capability,
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


def resolve_responses_route(*, api_type: str) -> ResponsesRoute:
    """按 api_type 能力矩阵选择 native 或 bridge。"""
    capability = get_provider_capability(api_type)
    if capability.create == TransportMode.UNSUPPORTED:
        return ResponsesRoute(
            transport=TransportMode.UNSUPPORTED,
            capability=capability,
            force_chat_completions_api=False,
            api_type=api_type,
        )
    if capability.create == TransportMode.NATIVE:
        return ResponsesRoute(
            transport=TransportMode.NATIVE,
            capability=capability,
            force_chat_completions_api=False,
            api_type=api_type,
        )
    return ResponsesRoute(
        transport=TransportMode.BRIDGE,
        capability=capability,
        force_chat_completions_api=True,
        api_type=api_type,
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
