"""组件凭据服务门面 — web 层与 core/provider_keys 之间的收口。"""

from __future__ import annotations

from typing import Any, Dict

from core.provider_keys import list_provider_keys, set_provider_key


class ProviderKeyServiceFacade:
    """组件凭据面板与 AI 工具的数据与操作面（值脱敏）。"""

    @staticmethod
    def list(domain: str = "") -> Dict[str, Any]:
        return {"providers": list_provider_keys(domain)}

    @staticmethod
    def set(provider: str, field: str, value: str) -> Dict[str, Any]:
        if not provider.strip() or not field.strip():
            raise ValueError("provider 与 field 不能为空")
        if not provider.replace("_", "").replace("-", "").isalnum():
            raise ValueError("provider 名仅限字母/数字/横线/下划线")
        if not field.replace("_", "").isalnum():
            raise ValueError("field 名仅限字母/数字/下划线")
        set_provider_key(provider.strip(), field.strip(), value)
        return {"ok": True, "provider": provider.strip(), "field": field.strip()}
