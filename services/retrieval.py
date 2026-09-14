"""检索服务门面 — web 层与 agent/retrieval 核心层之间的收口。"""

from __future__ import annotations

import time
from typing import Any, Dict, Tuple

from core.log import log
from core.sanitizer import sanitize_text


class RetrievalServiceFacade:
    """检索页签的数据聚合（能力 × 提供者矩阵、切换/启停/凭据、连通性测试、抓取设置）。"""

    # ------------------------------------------------------------------
    # 矩阵
    # ------------------------------------------------------------------

    def matrix(self) -> Dict[str, Any]:
        """能力 × 提供者矩阵快照（脱敏：凭据只暴露来源，不回显本体）。"""
        from agent.retrieval import providers
        from agent.retrieval.config import get_active
        from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS

        active: Dict[str, str] = {}
        for cap in CAPABILITY_PROTOCOLS:
            try:
                active[cap] = providers.resolve(cap).name
            except ValueError:
                active[cap] = ""
        return {
            "capabilities": list(CAPABILITY_PROTOCOLS),
            "selection": {cap: get_active(cap) for cap in CAPABILITY_PROTOCOLS},
            "active": active,
            "providers": [
                {
                    "name": p.name,
                    "display_name": p.display_name,
                    "description": p.description,
                    "enabled": p.enabled(),
                    "configured": p.configured(),
                    "requires_credential": p.requires_credential,
                    "credential_source": p.credential()[1],
                    "capabilities": providers.provider_capabilities(p),
                }
                for p in providers.list_providers()
            ],
        }

    def set_active(self, capability: str, name: str) -> Tuple[int, Any]:
        """切换指定能力的提供者（auto 恢复自动选择）。返回 (http_status, detail_or_matrix)。"""
        from agent.retrieval import providers
        from agent.retrieval.config import set_active as save_active
        from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS

        cap = capability.strip()
        if cap not in CAPABILITY_PROTOCOLS:
            return 404, f"未知能力: {cap}"
        name = name.strip()
        if name != "auto":
            try:
                providers.resolve(cap, name)  # 不支持/已禁用/未配置带原因抛出
            except ValueError as e:
                return 400, str(e)
        save_active(cap, name or "auto")
        return 200, self.matrix()

    def set_enabled(self, name: str, enabled: bool) -> Tuple[int, Any]:
        """启用/停用提供者。"""
        from agent.retrieval import providers
        from agent.retrieval.config import set_enabled as save_enabled
        try:
            providers.get_provider(name)
        except ValueError as e:
            return 404, str(e)
        save_enabled(name, enabled)
        return 200, self.matrix()

    def set_credential(self, name: str, api_key: str) -> Tuple[int, Any]:
        """配置提供者 API Key（空串清除）。"""
        from agent.retrieval import providers
        try:
            provider = providers.get_provider(name)
        except ValueError as e:
            return 404, str(e)
        if not provider.requires_credential:
            return 400, f"提供者 {name} 无需凭据"
        provider.set_api_key(api_key)
        return 200, self.matrix()

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------

    def test_provider(self, name: str, capability: str, user_input: str) -> Dict[str, Any]:
        """用真实调用测试提供者指定能力的连通性（同步，路由层投入线程池）。"""
        from agent.retrieval import providers
        from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS

        cap = capability.strip()
        if cap not in CAPABILITY_PROTOCOLS:
            return {"ok": False, "error": f"未知能力: {cap}"}
        try:
            provider = providers.resolve(cap, name)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        started = time.monotonic()
        try:
            summary, excerpt = self._run_capability_test(provider, cap, user_input.strip())
        except Exception as e:
            log(f"提供者能力测试失败 [{name}/{cap}]: {e}", "WARNING", tag="检索")
            return {"ok": False, "error": sanitize_text(str(e))[:500]}
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "summary": summary,
            "excerpt": excerpt,
        }

    @staticmethod
    def _run_capability_test(provider: object, capability: str, user_input: str) -> Tuple[str, str]:
        """按能力执行一次真实调用，返回 (summary, excerpt)。"""
        from agent.retrieval.providers.base import (
            CAP_READER,
            CAP_REPO,
            CAP_SEARCH,
            ReaderCap,
            RepoCap,
            SearchCap,
        )
        if capability == CAP_SEARCH and isinstance(provider, SearchCap):
            output = provider.search(user_input or "今日新闻", 3)
            refs = output.get("references", [])
            excerpt = "\n".join(f"{r.get('title', '')} — {r.get('url', '')}" for r in refs[:3])
            return f"{output.get('sources', 0)} 条结果", excerpt
        if capability == CAP_READER and isinstance(provider, ReaderCap):
            output = provider.read(user_input or "https://example.com", timeout=20)
            content = str(output.get("content", ""))
            return str(output.get("title") or output.get("url", "")), content[:300]
        if capability == CAP_REPO and isinstance(provider, RepoCap):
            content = provider.get_repo_structure(user_input or "vitejs/vite")
            return user_input or "vitejs/vite", content[:300]
        raise ValueError(f"提供者不支持该能力: {capability}")

    # ------------------------------------------------------------------
    # 抓取设置
    # ------------------------------------------------------------------

    def settings(self) -> Dict[str, Any]:
        """抓取设置快照（凭据不回显，仅返回是否已配置）。"""
        from agent.retrieval.config import full_config
        return full_config()

    def save_settings(self, *, proxy: "str | None" = None,
                      ssrf_protection: "bool | None" = None) -> Dict[str, Any]:
        """保存抓取设置（None 字段不变更）。"""
        from agent.retrieval.config import set_proxy
        from core.config import ConfigManager
        if proxy is not None:
            set_proxy(proxy)
        if ssrf_protection is not None:
            ConfigManager.set("retrieval_ssrf_protection", bool(ssrf_protection))
            ConfigManager.save()
        return self.settings()


_retrieval_service = RetrievalServiceFacade()


def get_retrieval_service() -> RetrievalServiceFacade:
    return _retrieval_service
