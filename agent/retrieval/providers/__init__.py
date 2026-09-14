"""检索提供者注册表：能力 × 提供者矩阵的统一解析入口。

解析优先级：显式指定（工具参数 provider）> 配置的固定选择
（retrieval_active.<capability>）> auto（按注册顺序取首个 启用 + 实现该能力 +
已配置凭据 的提供者）。注册顺序即 auto 模式优先级：reader 默认本地直连（免费），
search 默认首个可用组件。显式指定不做隐式回退——不可用即报明原因。

内置提供者：builtin（本地直连读取）、bigmodel（智谱检索/读取/仓库文档）；
第三方组件经 entities._sdk.register_retrieval_provider 注册（同名覆盖）。
"""

from __future__ import annotations

from typing import Dict, List

from agent.retrieval.providers.base import (
    CAPABILITY_LABELS,
    CAPABILITY_PROTOCOLS,
    Provider,
)
from agent.retrieval.providers.bigmodel import BigModelProvider
from agent.retrieval.providers.builtin import BuiltinProvider

CAPABILITIES: List[str] = list(CAPABILITY_PROTOCOLS.keys())

# 注册顺序即 auto 模式优先级（builtin 只实现 reader，自然不占 search 首位）
_PROVIDERS: Dict[str, Provider] = {}


def register(provider: Provider) -> None:
    """注册提供者（同名覆盖，组件热重载安全）。"""
    if not provider.name:
        raise ValueError("检索提供者缺少 name")
    _PROVIDERS[provider.name] = provider


def unregister(name: str) -> None:
    """注销提供者（组件热拔除时调用）。"""
    _PROVIDERS.pop(name, None)


def register_builtin_providers() -> None:
    """注册内置提供者（包导入时调用一次）。"""
    register(BuiltinProvider())
    register(BigModelProvider())


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
        raise ValueError(f"提供者 {provider.name} 已禁用（可在检索页签或 retrieval_providers 工具启用）")
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
    from agent.retrieval.config import get_active
    name = preferred.strip() or get_active(capability)
    if name and name != "auto":
        provider = get_provider(name)
        _check_usable(provider, capability)
        return provider
    for provider in _PROVIDERS.values():
        if _usable(provider, capability):
            return provider
    raise ValueError(
        f"无可用{CAPABILITY_LABELS.get(capability, capability)}提供者"
        f"（已注册: {', '.join(_PROVIDERS) or '无'}；请启用并配置凭据）"
    )


def reset() -> None:
    """清空注册表（测试用）。"""
    _PROVIDERS.clear()


register_builtin_providers()
