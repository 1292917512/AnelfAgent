"""DeepSeek 检索组件 — Search / Fetch 接入检索提供者矩阵。

DeepSeekSearchProvider 实现两个能力协议：
- SearchCap（search）：POST /v1/tools/search → 标准引用列表
- ReaderCap（read）：POST /v1/tools/fetch → Markdown 正文

凭据解析链：凭据中心 deepseek → llm_clients.json 的 deepseek.com 供应商 →
DEEPSEEK_API_KEY 环境变量。计费按成功请求（$0.75/千次），失败与空结果不计费。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from entities._sdk import (
    SOURCE_CONFIG,
    SOURCE_ENV,
    SOURCE_LLM,
    ProxyMode,
    RetrievalProvider,
    llm_provider_key,
    run_coro_sync,
)

# DeepSeek Fetch 由服务端读取整页再转 Markdown，预算高于工具层默认 15 秒
_FETCH_TIMEOUT_FLOOR = 30.0


class DeepSeekSearchProvider(RetrievalProvider):
    """DeepSeek Search（联网检索）+ Fetch（网页读取，Markdown 正文）。"""

    name = "deepseek"
    display_name = "DeepSeek"
    description = "DeepSeek Search/Fetch：联网检索 + 网页读取（Markdown），按成功请求计费（$0.75/千次）"
    key_hint = "凭据中心（检索页组件凭据）配置 deepseek，或 LLM 供应商（deepseek.com）与 DEEPSEEK_API_KEY 环境变量"

    def credential(self) -> Tuple[str, str]:
        from entities._sdk import get_provider_key
        value = get_provider_key("deepseek")
        if value:
            return value, SOURCE_CONFIG
        api_key, _provider_id = llm_provider_key("deepseek.com")
        if api_key:
            return api_key, SOURCE_LLM
        env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        return (env_key, SOURCE_ENV) if env_key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        from entities._sdk import set_provider_key
        set_provider_key("deepseek", "api_key", api_key)

    def _require_key(self) -> str:
        api_key, _source = self.credential()
        if not api_key:
            raise RuntimeError(f"DeepSeek 未配置凭据（{self.key_hint}）")
        return api_key

    # ── 检索能力 ────────────────────────────────────────────────

    def search(self, query: str, max_results: int) -> Dict[str, Any]:
        from entities.deepseek.client import deepseek_search, normalize_search_results
        data = run_coro_sync(deepseek_search(
            query, count=max_results, api_key=self._require_key(),
        ))
        return normalize_search_results(data, query, max_results)

    # ── 网页读取能力 ────────────────────────────────────────────

    def read(
        self,
        url: str,
        *,
        timeout: int = 15,
        extract_mode: str = "markdown",
        proxy_mode: ProxyMode | None = None,
        respect_robots: bool = False,
    ) -> Dict[str, Any]:
        """读取网页正文（服务端抓取并转 Markdown；robots 合规在 DeepSeek 侧执行）。"""
        if extract_mode != "markdown":
            raise ValueError("DeepSeek 网页读取仅支持 markdown 模式（text/raw 请改用 builtin 提供者）")
        from entities.deepseek.client import deepseek_fetch
        data = run_coro_sync(deepseek_fetch(
            url, api_key=self._require_key(),
            timeout=max(float(timeout), _FETCH_TIMEOUT_FLOOR),
            proxy_mode=proxy_mode if proxy_mode is not None else "system",
        ))
        return {
            "url": url,
            "extract_mode": "markdown",
            "title": str(data.get("title", "")),
            "content": str(data.get("content", "")),
        }

    # ── 错误分类 ────────────────────────────────────────────────

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        from entities.deepseek.client import deepseek_error_response
        return deepseek_error_response(
            exc, action, hint=hint or "检查 DeepSeek API Key 配置与网络连通性",
        )
