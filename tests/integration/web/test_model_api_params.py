"""模型接口参数可用性集成测试（由 scripts/test_model_api_params.py 改造为 pytest）。

覆盖：
1. LLMClientConfig 配置契约（数值范围 / 枚举 / 保留参数）
2. FastAPI Pydantic 请求模型（Create/Update Provider & Model）
3. 模型 CRUD HTTP 往返（TestClient，临时配置，不污染真实 llm_clients.json）
4. chat_protocol / request_params / extra_body 可用性
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent.llm.llm_client import API_TYPES, LLMClientConfig, ModelType
from agent.llm.protocol import CHAT_PROTOCOLS
from web.routers.models import (
    CreateModelReq,
    CreateProviderReq,
    UpdateModelReq,
    UpdateProviderReq,
    _normalize_model_params,
    _serialize_model_config,
)

# ---------------------------------------------------------------------------
# 1) 配置层契约
# ---------------------------------------------------------------------------

_VALID_CONFIG_CASES: list[tuple[str, dict[str, Any]]] = [
    ("defaults", {}),
    ("temperature_0", {"temperature": 0}),
    ("temperature_2", {"temperature": 2}),
    ("top_p_0", {"top_p": 0}),
    ("top_p_1", {"top_p": 1}),
    ("max_tokens_0", {"max_tokens": 0}),
    ("context_window_0", {"context_window": 0}),
    ("timeout_small", {"timeout": 0.001}),
    ("freq_penalty_-2", {"frequency_penalty": -2}),
    ("freq_penalty_2", {"frequency_penalty": 2}),
    ("presence_penalty_-2", {"presence_penalty": -2}),
    ("presence_penalty_2", {"presence_penalty": 2}),
    ("vision_base64", {"vision_format": "base64"}),
    ("vision_url", {"vision_format": "url"}),
    ("vision_both", {"vision_format": "both"}),
    ("request_params_ok", {"request_params": {"api_version": "2025-01-01"}}),
    ("extra_body_ok", {"extra_body": {"custom": True}}),
    ("supports_flags", {
        "supports_vision": True,
        "supports_tools": False,
        "supports_reasoning": True,
    }),
]
for _protocol in sorted(CHAT_PROTOCOLS):
    _VALID_CONFIG_CASES.append((f"chat_protocol_{_protocol}", {"chat_protocol": _protocol}))
for _api_type in API_TYPES:
    _VALID_CONFIG_CASES.append((f"api_type_{_api_type}", {"api_type": _api_type, "model": "m"}))
for _mt in ModelType:
    _VALID_CONFIG_CASES.append((f"model_types_{_mt.value}", {"model_types": [_mt.value]}))

_INVALID_CONFIG_CASES: list[tuple[str, dict[str, Any]]] = [
    ("temperature_high", {"temperature": 2.1}),
    ("temperature_low", {"temperature": -0.1}),
    ("top_p_high", {"top_p": 1.1}),
    ("top_p_low", {"top_p": -0.1}),
    ("max_tokens_neg", {"max_tokens": -1}),
    ("context_window_neg", {"context_window": -1}),
    ("timeout_zero", {"timeout": 0}),
    ("timeout_neg", {"timeout": -1}),
    ("api_type_unknown", {"api_type": "unknown"}),
    ("vision_format_bad", {"vision_format": "binary"}),
    ("chat_protocol_bad", {"chat_protocol": "websocket"}),
    ("model_types_bad", {"model_types": ["invalid"]}),
    ("request_params_reserved_model", {"request_params": {"model": "x"}}),
    ("request_params_reserved_messages", {"request_params": {"messages": []}}),
    ("request_params_reserved_stream", {"request_params": {"stream": True}}),
    ("request_params_not_object", {"request_params": []}),  # type: ignore[dict-item]
    ("extra_body_not_object", {"extra_body": "x"}),  # type: ignore[dict-item]
]


class TestLLMClientConfigContract:
    @pytest.mark.parametrize(("name", "kwargs"), _VALID_CONFIG_CASES, ids=[c[0] for c in _VALID_CONFIG_CASES])
    def test_valid_cases_accepted(self, name: str, kwargs: dict[str, Any]) -> None:
        LLMClientConfig(**kwargs)

    @pytest.mark.parametrize(("name", "kwargs"), _INVALID_CONFIG_CASES, ids=[c[0] for c in _INVALID_CONFIG_CASES])
    def test_invalid_cases_rejected(self, name: str, kwargs: dict[str, Any]) -> None:
        with pytest.raises((ValueError, TypeError)):
            LLMClientConfig(**kwargs)

    def test_serde_roundtrip(self) -> None:
        cfg = LLMClientConfig(
            name="demo",
            model="gpt-4o",
            chat_protocol="responses",
            request_params={"api_version": "v1"},
            extra_body={"x": 1},
            context_window=128000,
        )
        d = cfg.to_dict()
        m = cfg.to_model_dict()
        for key in (
            "chat_protocol", "request_params", "extra_body",
            "supports_vision", "supports_tools", "supports_reasoning",
            "vision_format", "context_window",
        ):
            assert key in d, f"to_dict 缺少 {key}"
            assert key in m, f"to_model_dict 缺少 {key}"
        # 采样参数仅完整字典格式携带（to_model_dict 为模型条目格式）
        for key in ("temperature", "top_p", "max_tokens", "timeout"):
            assert key in d, f"to_dict 缺少 {key}"
        restored = LLMClientConfig.from_dict(d)
        assert restored.chat_protocol == "responses"
        assert restored.request_params == {"api_version": "v1"}

    def test_api_schema_rejects(self) -> None:
        with pytest.raises(ValidationError):
            UpdateModelReq(temperature=3)
        with pytest.raises(ValidationError):
            CreateModelReq(id="m", timeout=0)
        with pytest.raises(ValidationError):
            CreateModelReq(id="m", chat_protocol="bad")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            CreateModelReq(id="m", request_params={"model": "other"})


# ---------------------------------------------------------------------------
# 2) API Schema 合法参数矩阵
# ---------------------------------------------------------------------------

class TestAPISchemaMatrix:
    def test_create_provider_req(self) -> None:
        CreateProviderReq(
            id="p1", name="P", base_url="https://api.openai.com/v1",
            api_key="sk-test", api_type="openai", proxy_url="",
        )

    def test_update_provider_req_partial(self) -> None:
        UpdateProviderReq(name="N", proxy_url="127.0.0.1:7890")

    def test_create_model_req_full(self) -> None:
        CreateModelReq(**{
            "id": "m1",
            "model": "gpt-4o",
            "model_types": ["chat"],
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 8192,
            "frequency_penalty": 0.1,
            "presence_penalty": -0.1,
            "timeout": 60.0,
            "context_window": 128000,
            "supports_vision": True,
            "supports_tools": True,
            "vision_format": "both",
            "supports_reasoning": False,
            "chat_protocol": "auto",
            "request_params": {"service_tier": "auto"},
            "extra_body": {"foo": "bar"},
        })

    @pytest.mark.parametrize("protocol", ["chat_completions", "responses", "auto"])
    def test_create_model_req_protocols(self, protocol: str) -> None:
        CreateModelReq(id="x", model="m", chat_protocol=protocol)

    def test_update_model_req_all_optional(self) -> None:
        UpdateModelReq(
            model="gpt-4.1",
            temperature=1.0,
            top_p=0.5,
            max_tokens=100,
            frequency_penalty=0,
            presence_penalty=0,
            timeout=30,
            context_window=8000,
            supports_vision=False,
            supports_tools=True,
            vision_format="url",
            supports_reasoning=True,
            chat_protocol="responses",
            request_params={"api_version": "2024-10-01"},
            extra_body={"n": 1},
        )

    def test_normalize_legacy_extra_params(self) -> None:
        req = CreateModelReq(
            id="legacy",
            model="m",
            request_params={"k": 1},
            extra_body={"a": 1},
            extra_params={"b": 2},
        )
        normalized = _normalize_model_params(req)
        assert normalized["request_params"] == {"k": 1}
        assert normalized["extra_body"] == {"b": 2, "a": 1}
        assert normalized["extra_params"] == {}
        # 未显式提供的字段不回填默认值（exclude_unset 语义，默认值由序列化层补齐）
        assert "chat_protocol" not in normalized

    def test_serialize_defaults_chat_protocol(self) -> None:
        out = _serialize_model_config({
            "id": "m",
            "extra_params": {"legacy": True},
            "extra_body": {"new": True},
        })
        assert out["chat_protocol"] == "chat_completions"
        assert out["extra_body"] == {"legacy": True, "new": True}
        assert "extra_params" not in out


# ---------------------------------------------------------------------------
# 3) HTTP 往返（临时配置目录）
# ---------------------------------------------------------------------------

@pytest.fixture()
def probe_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """指向临时配置的 TestClient，测试后还原路径与单例。"""
    from fastapi.testclient import TestClient

    import agent.llm.llm_manager as mgr_mod
    import services.model as model_svc_mod
    import web.routers.models as models_router
    from agent.llm.llm_manager import LLMManager
    from core.path import ConfigPaths

    llm_cfg = tmp_path / "llm_clients.json"
    webui_cfg = tmp_path / "webui.json"
    llm_cfg.write_text(json.dumps({
        "providers": [],
        "type_priorities": {},
        "default_chat": "",
    }), encoding="utf-8")
    webui_cfg.write_text(json.dumps({
        "auth": {"password": "", "api_keys": []},
        "server": {"host": "127.0.0.1", "port": 8092},
    }), encoding="utf-8")

    monkeypatch.setattr(ConfigPaths, "LLM_CLIENTS", str(llm_cfg))
    monkeypatch.setattr(ConfigPaths, "WEBUI_CONFIG", str(webui_cfg))
    monkeypatch.setattr(mgr_mod, "_manager", LLMManager(config_path=str(llm_cfg)), raising=False)

    from web.server import create_app
    app = create_app()
    # 确保路由层使用同一临时 manager
    models_router._svc = model_svc_mod.ModelService()
    yield TestClient(app), llm_cfg
    monkeypatch.setattr(mgr_mod, "_manager", None, raising=False)


class TestHTTPRoundtrip:
    def test_models_crud_roundtrip(self, probe_client) -> None:
        client, llm_cfg = probe_client

        # 创建供应商
        r = client.post("/api/models/providers", json={
            "id": "probe_provider",
            "name": "Probe",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-real-secret-key-for-mask-test",
            "api_type": "openai",
            "proxy_url": "",
        })
        assert r.status_code == 200, r.text

        # 列表脱敏 api_key
        r = client.get("/api/models/providers")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data, "应有供应商"
        key = data[0]["api_key"]
        assert "sk-real-secret-key-for-mask-test" not in key
        assert "****" in key or key == ""

        # 全参数创建模型
        r = client.post("/api/models/providers/probe_provider/models", json={
            "id": "probe_model",
            "model": "gpt-4o",
            "model_types": ["chat"],
            "temperature": 0.5,
            "top_p": 0.8,
            "max_tokens": 4096,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "timeout": 90.0,
            "context_window": 128000,
            "supports_vision": True,
            "supports_tools": True,
            "vision_format": "base64",
            "supports_reasoning": False,
            "chat_protocol": "auto",
            "request_params": {"service_tier": "auto"},
            "extra_body": {"demo": True},
        })
        assert r.status_code == 200, r.text

        # 读取回显参数且不泄露密钥
        r = client.get("/api/models/probe_model")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["chat_protocol"] == "auto"
        assert body["request_params"] == {"service_tier": "auto"}
        assert body["extra_body"] == {"demo": True}
        assert body["temperature"] == 0.5
        assert body["context_window"] == 128000
        assert "sk-real-secret-key-for-mask-test" not in r.text

        # 更新协议与参数
        r = client.put("/api/models/probe_model", json={
            "chat_protocol": "responses",
            "temperature": 0.2,
            "request_params": {"api_version": "2025-01-01"},
            "extra_body": {"n": 1},
        })
        assert r.status_code == 200, r.text
        body = client.get("/api/models/probe_model").json()
        assert body["chat_protocol"] == "responses"
        assert body["temperature"] == 0.2
        assert body["request_params"] == {"api_version": "2025-01-01"}
        assert body["extra_body"] == {"n": 1}

        # 非法参数拒绝
        r = client.put("/api/models/probe_model", json={"temperature": 9})
        assert r.status_code == 422, r.text
        r = client.put("/api/models/probe_model", json={
            "request_params": {"model": "hijack"},
        })
        assert r.status_code == 422, r.text
        r = client.put("/api/models/probe_model", json={
            "chat_protocol": "websocket",
        })
        assert r.status_code == 422, r.text

        # 脱敏密钥回传更新时保留真实密钥
        masked = client.get("/api/models/providers").json()[0]["api_key"]
        r = client.put("/api/models/providers/probe_provider", json={
            "api_key": masked,
            "name": "Probe2",
        })
        assert r.status_code == 200, r.text
        raw = json.loads(llm_cfg.read_text("utf-8"))
        assert raw["providers"][0]["api_key"] == "sk-real-secret-key-for-mask-test"

        # 默认模型与优先级
        r = client.put("/api/models/config/default", json={"model_id": "probe_model"})
        assert r.status_code == 200, r.text
        p = client.get("/api/models/priorities")
        assert p.status_code == 200, p.text
        assert "probe_model" in [x["id"] for x in p.json().get("chat", [])]

        # model-info 端点
        r = client.post("/api/models/model-info", json={
            "model": "gpt-4o",
            "api_type": "openai",
        })
        assert r.status_code == 200, r.text

        # 清理
        r = client.delete("/api/models/probe_model")
        assert r.status_code == 200, r.text
        r = client.delete("/api/models/providers/probe_provider")
        assert r.status_code == 200, r.text
