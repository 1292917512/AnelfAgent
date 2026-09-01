"""entities/media provider 路由器单元测试：优先级链、跳过、降级、错误聚合。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

import entities.media.providers as providers_mod
from entities.media.providers import run_capability
from entities.media.providers.base import (
    CapabilityNotSupported,
    MediaProvider,
    ProviderUnavailable,
)


class _FakeProvider(MediaProvider):
    """可编排行为的假 provider。"""

    def __init__(
        self,
        name: str,
        caps: frozenset,
        configured: bool = True,
        result: Dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.capabilities = caps
        self._configured = configured
        self._result = result or {"data": "ok"}
        self._error = error
        self.calls: list = []

    def is_configured(self, capability: str) -> bool:
        return self._configured

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append((capability, kwargs))
        if self._error:
            raise self._error
        return dict(self._result)


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch):
    """隔离 provider 注册表与优先级链配置。"""
    registry: Dict[str, MediaProvider] = {}
    monkeypatch.setattr(providers_mod, "_PROVIDERS", registry)
    monkeypatch.setattr(
        providers_mod, "provider_chain",
        lambda cap: ["models", "minimax"],
    )
    return registry


class TestChainRouting:
    async def test_primary_success(self, _registry):
        models = _FakeProvider("models", frozenset({"tts"}), result={"audio": 1})
        minimax = _FakeProvider("minimax", frozenset({"tts"}))
        _registry["models"] = models
        _registry["minimax"] = minimax
        out = await run_capability("tts", "语音合成", text="hi")
        assert out["success"] is True
        assert out["provider"] == "models"
        assert "fallback_from" not in out
        assert minimax.calls == []

    async def test_fallback_on_failure(self, _registry):
        models = _FakeProvider("models", frozenset({"tts"}), error=RuntimeError("HTTP 500 boom"))
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 2})
        _registry["models"] = models
        _registry["minimax"] = minimax
        out = await run_capability("tts", "语音合成", text="hi")
        assert out["success"] is True
        assert out["provider"] == "minimax"
        assert out["fallback_from"] == ["models"]
        assert "HTTP 500" in out["primary_error"]

    async def test_skip_unconfigured(self, _registry):
        models = _FakeProvider("models", frozenset({"tts"}), configured=False)
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 3})
        _registry["models"] = models
        _registry["minimax"] = minimax
        out = await run_capability("tts", "语音合成", text="hi")
        assert out["success"] is True
        assert out["provider"] == "minimax"
        assert models.calls == []
        assert "fallback_from" not in out  # 跳过不算失败

    async def test_capability_not_supported_falls_through(self, _registry):
        models = _FakeProvider("models", frozenset({"tts"}), error=CapabilityNotSupported("不支持"))
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 4})
        _registry["models"] = models
        _registry["minimax"] = minimax
        out = await run_capability("tts", "语音合成", text="hi")
        assert out["provider"] == "minimax"

    async def test_not_implemented_falls_through(self, _registry):
        models = _FakeProvider("models", frozenset({"voice_mgmt"}), error=NotImplementedError("协议不支持"))
        minimax = _FakeProvider("minimax", frozenset({"voice_mgmt"}), result={"voice_id": "v1"})
        _registry["models"] = models
        _registry["minimax"] = minimax
        out = await run_capability("voice_mgmt", "音色查询", op="list")
        assert out["provider"] == "minimax"

    async def test_provider_not_in_chain_capability(self, _registry):
        """链上 provider 未声明该能力时跳过。"""
        models = _FakeProvider("models", frozenset({"asr"}))  # 不含 tts
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 5})
        _registry["models"] = models
        _registry["minimax"] = minimax
        out = await run_capability("tts", "语音合成", text="hi")
        assert out["provider"] == "minimax"
        assert models.calls == []


class TestProviderOverride:
    async def test_explicit_provider(self, _registry):
        models = _FakeProvider("models", frozenset({"tts"}), error=AssertionError("不应被调用"))
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 6})
        _registry["models"] = models
        _registry["minimax"] = minimax
        out = await run_capability("tts", "语音合成", provider="minimax", text="hi")
        assert out["provider"] == "minimax"
        assert models.calls == []

    async def test_unknown_provider(self, _registry):
        out = await run_capability("tts", "语音合成", provider="nope", text="hi")
        assert "error" in out
        assert out.get("cause") == "param"

    async def test_explicit_provider_not_supporting_capability(self, _registry):
        """显式指定不支持该能力的 provider → PARAM 错误并告知谁支持。"""
        _registry["models"] = _FakeProvider("models", frozenset({"music"}))
        _registry["minimax"] = _FakeProvider("minimax", frozenset({"tts"}))
        out = await run_capability("music", "音乐生成", provider="minimax", op="generate")
        assert "error" in out
        assert out.get("cause") == "param"
        assert out.get("retryable") is False
        assert "models" in out.get("hint", "")
        assert "auto" in out.get("hint", "")


class TestErrorAggregation:
    async def test_all_fail(self, _registry):
        _registry["models"] = _FakeProvider("models", frozenset({"tts"}), error=RuntimeError("HTTP 401 unauthorized"))
        _registry["minimax"] = _FakeProvider("minimax", frozenset({"tts"}), error=RuntimeError("timeout"))
        out = await run_capability("tts", "语音合成", text="hi")
        assert "error" in out
        assert out.get("cause") == "config"  # 401 归因优先
        assert "models" in out["errors"] and "minimax" in out["errors"]

    async def test_all_skipped(self, _registry):
        _registry["models"] = _FakeProvider("models", frozenset({"tts"}), configured=False)
        _registry["minimax"] = _FakeProvider("minimax", frozenset({"tts"}), configured=False)
        out = await run_capability("tts", "语音合成", text="hi")
        assert "error" in out
        assert out.get("cause") == "config"
        assert out.get("retryable") is False
        assert "skipped" in out

    async def test_model_errors_merged(self, _registry):
        from entities.media.providers.base import ModelChainError
        exc = ModelChainError("所有 tts 模型均调用失败", {"minimax-tts": "HTTP 429 限流"})
        _registry["models"] = _FakeProvider("models", frozenset({"tts"}), error=exc)
        _registry["minimax"] = _FakeProvider("minimax", frozenset({"tts"}), error=ProviderUnavailable("未配置"))
        out = await run_capability("tts", "语音合成", text="hi")
        assert "minimax-tts" in out["errors"]
        assert out.get("cause") == "network"  # 429 → 限流可重试
        assert out.get("retryable") is True
