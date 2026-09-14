"""能力路由框架（agent.capabilities）单元测试：优先级链、跳过、降级、错误聚合、错误归因。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from agent.capabilities import (
    CapabilityNotSupported,
    CapabilityRouter,
    ProviderChainError,
    ProviderUnavailable,
    classify_provider_errors,
)
from core.tool_errors import ErrorCause


class _FakeProvider:
    """可编排行为的假提供者。"""

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

    def status_details(self, capability: str) -> Dict[str, Any]:
        return {}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append((capability, kwargs))
        if self._error:
            raise self._error
        return dict(self._result)


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch):
    """隔离配置的路由器（链配置键返回 None → 默认链 + 组件自动入链）。"""
    from core.config import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: None))
    return CapabilityRouter(config_key="test_priority", default_chain=["models"], log_tag="测试")


class TestChainSemantics:
    def test_component_auto_joins_default_chain(self, router):
        """未显式配置链时，已注册组件自动追加到默认链尾（即插即用）。"""
        router.register(_FakeProvider("models", frozenset({"tts"})))
        router.register(_FakeProvider("minimax", frozenset({"tts"})))
        assert router.chain("tts") == ["models", "minimax"]

    def test_configured_chain_respected_exactly(self, router, monkeypatch: pytest.MonkeyPatch):
        """显式配置非空链时严格按配置（可用于排除组件）。"""
        from core.config import ConfigManager
        monkeypatch.setattr(
            ConfigManager, "get",
            staticmethod(lambda k, d=None: {"tts": ["minimax"]}),
        )
        router.register(_FakeProvider("models", frozenset({"tts"})))
        router.register(_FakeProvider("minimax", frozenset({"tts"})))
        assert router.chain("tts") == ["minimax"]

    def test_register_requires_name_and_capabilities(self, router):
        with pytest.raises(ValueError):
            router.register(_FakeProvider("", frozenset({"tts"})))
        with pytest.raises(ValueError):
            router.register(_FakeProvider("x", frozenset()))


class TestChainRouting:
    async def test_primary_success(self, router):
        models = _FakeProvider("models", frozenset({"tts"}), result={"audio": 1})
        minimax = _FakeProvider("minimax", frozenset({"tts"}))
        router.register(models)
        router.register(minimax)
        out = await router.run("tts", "语音合成", text="hi")
        assert out["success"] is True
        assert out["provider"] == "models"
        assert "fallback_from" not in out
        assert minimax.calls == []

    async def test_fallback_on_failure(self, router):
        models = _FakeProvider("models", frozenset({"tts"}), error=RuntimeError("HTTP 500 boom"))
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 2})
        router.register(models)
        router.register(minimax)
        out = await router.run("tts", "语音合成", text="hi")
        assert out["success"] is True
        assert out["provider"] == "minimax"
        assert out["fallback_from"] == ["models"]
        assert "HTTP 500" in out["primary_error"]

    async def test_skip_unconfigured(self, router):
        models = _FakeProvider("models", frozenset({"tts"}), configured=False)
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 3})
        router.register(models)
        router.register(minimax)
        out = await router.run("tts", "语音合成", text="hi")
        assert out["success"] is True
        assert out["provider"] == "minimax"
        assert models.calls == []
        assert "fallback_from" not in out  # 跳过不算失败

    async def test_capability_not_supported_falls_through(self, router):
        models = _FakeProvider("models", frozenset({"tts"}), error=CapabilityNotSupported("不支持"))
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 4})
        router.register(models)
        router.register(minimax)
        out = await router.run("tts", "语音合成", text="hi")
        assert out["provider"] == "minimax"

    async def test_not_implemented_falls_through(self, router):
        models = _FakeProvider("models", frozenset({"voice_mgmt"}), error=NotImplementedError("协议不支持"))
        minimax = _FakeProvider("minimax", frozenset({"voice_mgmt"}), result={"voice_id": "v1"})
        router.register(models)
        router.register(minimax)
        out = await router.run("voice_mgmt", "音色查询", op="list")
        assert out["provider"] == "minimax"

    async def test_provider_not_in_chain_capability(self, router):
        """链上提供者未声明该能力时跳过。"""
        models = _FakeProvider("models", frozenset({"asr"}))  # 不含 tts
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 5})
        router.register(models)
        router.register(minimax)
        out = await router.run("tts", "语音合成", text="hi")
        assert out["provider"] == "minimax"
        assert models.calls == []


class TestProviderOverride:
    async def test_explicit_provider(self, router):
        models = _FakeProvider("models", frozenset({"tts"}), error=AssertionError("不应被调用"))
        minimax = _FakeProvider("minimax", frozenset({"tts"}), result={"audio": 6})
        router.register(models)
        router.register(minimax)
        out = await router.run("tts", "语音合成", provider="minimax", text="hi")
        assert out["provider"] == "minimax"
        assert models.calls == []

    async def test_unknown_provider(self, router):
        out = await router.run("tts", "语音合成", provider="nope", text="hi")
        assert "error" in out
        assert out.get("cause") == "param"

    async def test_explicit_provider_not_supporting_capability(self, router):
        """显式指定不支持该能力的提供者 → PARAM 错误并告知谁支持。"""
        router.register(_FakeProvider("models", frozenset({"music"})))
        router.register(_FakeProvider("minimax", frozenset({"tts"})))
        out = await router.run("music", "音乐生成", provider="minimax", op="generate")
        assert "error" in out
        assert out.get("cause") == "param"
        assert out.get("retryable") is False
        assert "models" in out.get("hint", "")
        assert "auto" in out.get("hint", "")


class TestErrorAggregation:
    async def test_all_fail(self, router):
        router.register(_FakeProvider("models", frozenset({"tts"}), error=RuntimeError("HTTP 401 unauthorized")))
        router.register(_FakeProvider("minimax", frozenset({"tts"}), error=RuntimeError("timeout")))
        out = await router.run("tts", "语音合成", text="hi")
        assert "error" in out
        assert out.get("cause") == "config"  # 401 归因优先
        assert "models" in out["errors"] and "minimax" in out["errors"]

    async def test_all_skipped(self, router):
        router.register(_FakeProvider("models", frozenset({"tts"}), configured=False))
        router.register(_FakeProvider("minimax", frozenset({"tts"}), configured=False))
        out = await router.run("tts", "语音合成", text="hi")
        assert "error" in out
        assert out.get("cause") == "config"
        assert out.get("retryable") is False
        assert "skipped" in out

    async def test_model_errors_merged(self, router):
        exc = ProviderChainError("所有 tts 模型均调用失败", {"minimax-tts": "HTTP 429 限流"})
        router.register(_FakeProvider("models", frozenset({"tts"}), error=exc))
        router.register(_FakeProvider("minimax", frozenset({"tts"}), error=ProviderUnavailable("未配置")))
        out = await router.run("tts", "语音合成", text="hi")
        assert "minimax-tts" in out["errors"]
        assert out.get("cause") == "network"  # 429 → 限流可重试
        assert out.get("retryable") is True


class TestStatus:
    def test_status_reports_providers_and_chains(self, router):
        router.register(_FakeProvider("models", frozenset({"tts", "music"}), configured=True))
        router.register(_FakeProvider("minimax", frozenset({"tts"}), configured=False))
        status = router.status(["tts", "music"])
        assert status["chains"]["tts"] == ["models", "minimax"]
        assert status["chains"]["music"] == ["models"]
        providers = {p["name"]: p for p in status["providers"]}
        assert providers["models"]["configured"] == {"music": True, "tts": True}
        assert providers["minimax"]["configured"] == {"tts": False}


class TestClassifyProviderErrors:
    def test_auth(self) -> None:
        cause, retryable, hint = classify_provider_errors(
            {"m1": "HTTP 401: missing api secret key (1004)"}
        )
        assert cause == ErrorCause.CONFIG
        assert not retryable
        assert "密钥" in hint

    def test_balance(self) -> None:
        cause, retryable, hint = classify_provider_errors(
            {"m1": "MiniMax API 错误 (1008): 余额不足"}
        )
        assert cause == ErrorCause.CONFIG
        assert not retryable
        assert "余额" in hint

    def test_sensitive_content(self) -> None:
        cause, retryable, hint = classify_provider_errors(
            {"m1": "HTTP 422: sensitive content (1026)"}
        )
        assert cause == ErrorCause.PARAM
        assert not retryable
        assert "敏感" in hint

    def test_timeout(self) -> None:
        cause, retryable, _ = classify_provider_errors(
            {"m1": "视频生成超时（600s 内未完成）"}
        )
        assert cause == ErrorCause.TIMEOUT
        assert retryable

    def test_default_network(self) -> None:
        cause, retryable, hint = classify_provider_errors(
            {"m1": "HTTP 500: internal error (1000)"}
        )
        assert cause == ErrorCause.NETWORK
        assert retryable
        assert hint

    def test_expired_link(self) -> None:
        cause, retryable, hint = classify_provider_errors({
            "models": "无法下载图片（链接可能已过期）: https://cdn.example.com/img?token=...",
            "minimax": "图片下载失败（HTTP 400），链接已过期或失效",
        })
        assert cause == ErrorCause.NOT_FOUND
        assert not retryable
        assert "过期" in hint
