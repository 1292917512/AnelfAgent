"""MiniMax 提供者：Coding Plan 检索能力（订阅配额，不计 API 调用费）。

凭据解析链：entities/minimax/config.json 的 coding_plan_api_key → api_key
→ llm_clients.json 中 minimaxi.com 供应商凭据 → MINIMAX_API_KEY 环境变量。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from entities.web.providers.base import (
    SOURCE_CONFIG,
    SOURCE_ENV,
    SOURCE_LLM,
    Provider,
    llm_provider_key,
    run_coro_sync,
)

_SEARCH_TIMEOUT = 15.0


class MinimaxProvider(Provider):
    """MiniMax Coding Plan 网页检索。"""

    name = "minimax"
    display_name = "MiniMax"
    description = "MiniMax Coding Plan 联网检索（订阅配额，不计 API 调用费）"
    key_hint = "配置 entities/minimax/config.json 的 coding_plan_api_key，或 LLM 供应商（minimaxi.com）的 API Key"

    def credential(self) -> Tuple[str, str]:
        from entities.minimax.client import get_config
        for key in ("coding_plan_api_key", "api_key"):
            value = str(get_config(key) or "").strip()
            if value:
                return value, SOURCE_CONFIG
        api_key, _provider_id = llm_provider_key("minimaxi.com", "minimax.io")
        if api_key:
            return api_key, SOURCE_LLM
        env_key = os.environ.get("MINIMAX_API_KEY", "").strip()
        return (env_key, SOURCE_ENV) if env_key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        from entities.minimax.client import update_config
        update_config({"coding_plan_api_key": api_key.strip()})

    def search(self, query: str, max_results: int) -> Dict[str, Any]:
        api_key, _source = self.credential()
        if not api_key:
            raise RuntimeError(f"MiniMax Coding Plan 未配置凭据（{self.key_hint}）")
        from entities.minimax.client import MiniMaxClient, normalize_search_results
        client = MiniMaxClient()
        data = run_coro_sync(client.coding_plan_search(query, timeout=_SEARCH_TIMEOUT, api_key=api_key))
        return normalize_search_results(data, query, max_results)

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        from entities.minimax.client import minimax_error_response
        return minimax_error_response(
            exc, action,
            hint=hint or "检查网络连通性，以及 MiniMax Coding Plan 凭据配置",
        )
