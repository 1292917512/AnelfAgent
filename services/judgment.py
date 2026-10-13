"""判断能力管理门面；连接目录与诊断由 agent.judgment 统一提供。"""
from __future__ import annotations

from typing import Any

from agent.judgment.catalog import CatalogAction, public_catalog, update_catalog
from agent.judgment.diagnostics import check_connection, test_inference
from agent.judgment.engine import load_config
from agent.judgment.health import judgment_health
from agent.judgment.types import JudgmentError
from core.sanitizer import sanitize_text


class JudgmentService:
    """供 Web 调用的模型目录、状态与测试接口。"""

    @staticmethod
    def status() -> dict[str, Any]:
        try:
            cfg = load_config()
        except JudgmentError as exc:
            return {"enabled": False, "channel": "unavailable", "error": sanitize_text(str(exc))}
        return {
            "enabled": cfg.enabled, "channel": cfg.channel,
            "api_key_configured": bool(cfg.api_key), "base_url": cfg.base_url,
            "use_proxy": cfg.use_proxy,
            "model": cfg.model, "model_id": cfg.model_id, "provider_id": cfg.provider_id,
            "timeout": cfg.timeout, "fallback_enabled": cfg.fallback_enabled,
            "fallback_model": cfg.fallback_model, "fallback_effort": cfg.fallback_effort,
        }

    @staticmethod
    def catalog() -> dict[str, Any]:
        return {**public_catalog(), "health": judgment_health.snapshot()}

    @staticmethod
    def update(action: CatalogAction, identifier: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return update_catalog(action, identifier, data)

    @staticmethod
    async def test(model_id: str = "", *, inference: bool = False) -> dict[str, Any]:
        return await test_inference(model_id) if inference else await check_connection(model_id)


judgment_service = JudgmentService()
