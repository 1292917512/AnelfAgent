"""组件凭据服务门面 — web 层与 core/provider_keys 之间的收口。"""

from __future__ import annotations

from typing import Any

from core.provider_keys import list_provider_keys, set_provider_fields


class ProviderKeyServiceFacade:
    """组件凭据面板与 AI 工具的数据与操作面（值脱敏）。"""

    @staticmethod
    def list(domain: str = "") -> dict[str, Any]:
        return {"providers": list_provider_keys(domain)}

    @staticmethod
    def set(provider: str, values: dict[str, str]) -> dict[str, Any]:
        set_provider_fields(provider, values)
        return {"ok": True, "provider": provider}
