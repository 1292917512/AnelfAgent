"""提供者注册表：能力 × 提供者矩阵的统一解析入口。

解析优先级：显式指定（工具参数 provider）> 配置的固定选择（web 实体 config.json
的 active.<capability>）> auto（按注册顺序取首个 启用 + 实现该能力 + 已配置凭据
的提供者）。注册顺序即 auto 模式优先级：reader 默认本地直连（免费），search
默认 MiniMax。显式指定不做隐式回退——不可用即报明原因（不支持/已禁用/未配置）。
"""

from __future__ import annotations

from typing import Dict, List

from entities.web.providers.base import (
    CAPABILITY_LABELS,
    CAPABILITY_PROTOCOLS,
    Provider,
)
from entities.web.providers.bigmodel import BigModelProvider
from entities.web.providers.builtin import BuiltinProvider
from entities.web.providers.minimax import MinimaxProvider

CAPABILITIES: List[str] = list(CAPABILITY_PROTOCOLS.keys())

# 注册顺序即 auto 模式优先级（builtin 只实现 reader，自然不占 search 首位）
_PROVIDERS: Dict[str, Provider] = {
    provider.name: provider
    for provider in (BuiltinProvider(), MinimaxProvider(), BigModelProvider())
}


def list_providers() -> List[Provider]:
    """列出全部已注册提供者。"""
    return list(_PROVIDERS.values())


def get_provider(name: str) -> Provider:
    """按名称获取提供者，未知名称抛 ValueError。"""
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise ValueError(f"未知提供者: {name}（可选: {', '.join(_PROVIDERS)}）")
    return provider


def provider_capabilities(provider: Provider) -> List[str]:
    """提供者实现的能力列表（按能力注册顺序）。"""
    return [cap for cap, proto in CAPABILITY_PROTOCOLS.items() if isinstance(provider, proto)]


def _check_usable(provider: Provider, capability: str) -> None:
    """校验提供者对指定能力可用，不可用抛 ValueError 并说明原因。"""
    label = CAPABILITY_LABELS.get(capability, capability)
    if not isinstance(provider, CAPABILITY_PROTOCOLS[capability]):
        raise ValueError(f"提供者 {provider.name} 不支持{label}能力")
    if not provider.enabled():
        raise ValueError(f"提供者 {provider.name} 已禁用（可在 Web 面板或 web_providers 工具启用）")
    if not provider.configured():
        raise ValueError(f"提供者 {provider.name} 未配置凭据（{provider.key_hint}）")


def _usable(provider: Provider, capability: str) -> bool:
    try:
        _check_usable(provider, capability)
    except ValueError:
        return False
    return True


def any_available(capability: str) -> bool:
    """是否存在可用提供者（工具 check_fn 门控用）。"""
    return any(_usable(p, capability) for p in _PROVIDERS.values())


def resolve(capability: str, preferred: str = "") -> Provider:
    """解析指定能力的生效提供者（见模块 docstring 的优先级）。"""
    if capability not in CAPABILITY_PROTOCOLS:
        raise ValueError(f"未知能力: {capability}（可选: {', '.join(CAPABILITIES)}）")
    from entities.web.web_config import get_active
    name = preferred.strip() or get_active(capability)
    if name and name != "auto":
        provider = get_provider(name)
        _check_usable(provider, capability)
        return provider
    for provider in _PROVIDERS.values():
        if _usable(provider, capability):
            return provider
    label = CAPABILITY_LABELS.get(capability, capability)
    raise ValueError(f"没有可用的{label}提供者（全部未配置凭据或已禁用）")
