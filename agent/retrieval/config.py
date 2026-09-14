"""检索核心配置 — 统一配置体系（ConfigManager）存取。

配置键（retrieval 组，配置中心/检索页签可见）：
- retrieval_proxy：抓取代理地址（空=不使用）
- retrieval_active：能力 × 固定提供者选择（JSON 字典，缺省 auto 自动选择）
- retrieval_disabled_providers：停用的提供者（JSON 数组）
- retrieval_bigmodel_api_key：智谱 BigModel 凭据（password 类型，掩码展示）
- retrieval_ssrf_protection：SSRF 防护开关
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.config import ConfigManager


def _save(key: str, value: Any) -> None:
    ConfigManager.set(key, value)
    ConfigManager.save()


def get_proxy() -> str:
    """抓取代理地址（空字符串表示不使用代理）。"""
    return str(ConfigManager.get("retrieval_proxy", "") or "")


def set_proxy(proxy: str) -> None:
    _save("retrieval_proxy", proxy.strip())


def get_active(capability: str) -> str:
    """指定能力配置的固定提供者（auto 表示自动选择）。"""
    active = ConfigManager.get("retrieval_active", {})
    if isinstance(active, dict):
        return str(active.get(capability, "auto")) or "auto"
    return "auto"


def set_active(capability: str, name: str) -> None:
    """设置指定能力的固定提供者（auto 恢复自动选择）。"""
    current = ConfigManager.get("retrieval_active", {})
    merged = dict(current) if isinstance(current, dict) else {}
    merged[capability] = name.strip() or "auto"
    _save("retrieval_active", merged)


def is_enabled(provider: str) -> bool:
    """提供者启用状态（默认启用，停用列表中的为禁用）。"""
    disabled = ConfigManager.get("retrieval_disabled_providers", [])
    return not (isinstance(disabled, list) and provider in disabled)


def set_enabled(provider: str, enabled: bool) -> None:
    """设置提供者启用状态。"""
    current = ConfigManager.get("retrieval_disabled_providers", [])
    disabled: List[str] = list(current) if isinstance(current, list) else []
    if enabled:
        disabled = [name for name in disabled if name != provider]
    elif provider not in disabled:
        disabled.append(provider)
    _save("retrieval_disabled_providers", disabled)


def get_provider_key(name: str) -> str:
    """获取内置提供者持久化的 API Key（无则空串）。"""
    return str(ConfigManager.get(f"retrieval_{name}_api_key", "") or "").strip()


def set_provider_key(name: str, api_key: str) -> None:
    """持久化内置提供者 API Key（空串表示清除）。"""
    _save(f"retrieval_{name}_api_key", api_key.strip())


def full_config() -> Dict[str, Any]:
    """检索配置快照（凭据不回显，仅返回是否已配置）。"""
    return {
        "proxy": get_proxy(),
        "active": ConfigManager.get("retrieval_active", {}),
        "disabled_providers": ConfigManager.get("retrieval_disabled_providers", []),
        "ssrf_protection": bool(ConfigManager.get("retrieval_ssrf_protection", True)),
        "bigmodel_key_configured": bool(get_provider_key("bigmodel")),
    }
