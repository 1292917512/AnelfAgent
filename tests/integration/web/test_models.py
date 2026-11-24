from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent.llm.llm_client import LLMClientConfig
from services.model import ModelService

_serialize_model_config = ModelService().serialize_model_config

from web.routers.models import (
    CreateModelReq,
    CreateProviderReq,
    UpdateModelReq,
    _normalize_model_params,
)


class FakeManager:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(api_key="secret-provider-key")
        self.updated: dict = {}

    def list_providers(self):
        return [{
            "id": "provider",
            "name": "Provider",
            "api_key": self.provider.api_key,
        }]

    def get_provider(self, provider_id: str):
        return self.provider if provider_id == "provider" else None

    def get_client(self, model_id: str):
        if model_id != "model":
            return None
        return SimpleNamespace(config=LLMClientConfig(
            name="model",
            model="gpt-4.1",
            provider_id="provider",
            api_key=self.provider.api_key,
        ))

    def update_provider(self, provider_id: str, **kwargs):
        self.updated = kwargs
        return provider_id == "provider"


def test_service_masks_all_read_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ModelService()
    manager = FakeManager()
    monkeypatch.setattr(service, "_manager", lambda: manager)

    providers = service.list_providers()
    model = service.get_model_config("model")

    assert "secret-provider-key" not in providers[0]["api_key"]
    assert model is not None
    assert "secret-provider-key" not in model["api_key"]


def test_masked_or_empty_key_preserves_existing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ModelService()
    manager = FakeManager()
    monkeypatch.setattr(service, "_manager", lambda: manager)

    assert service.update_provider("provider", api_key="secr****-key", name="New")
    assert manager.updated == {"name": "New"}


def test_error_sanitization_removes_configured_and_request_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ModelService()
    manager = FakeManager()
    monkeypatch.setattr(service, "_manager", lambda: manager)

    result = service.sanitize_error(
        RuntimeError("secret-provider-key request-secret"),
        "request-secret",
    )

    assert "secret-provider-key" not in result
    assert "request-secret" not in result


def test_extended_parameters_keep_top_level_and_body_separate() -> None:
    request = CreateModelReq(
        id="model",
        model="gpt-4.1",
        request_params={"api_version": "2025-01-01"},
        extra_body={"custom": True},
        extra_params={"legacy": 1},
    )

    normalized = _normalize_model_params(request)

    assert normalized["request_params"] == {"api_version": "2025-01-01"}
    assert normalized["extra_body"] == {"legacy": 1, "custom": True}
    assert normalized["extra_params"] == {}


def test_serialization_merges_legacy_extra_body() -> None:
    serialized = _serialize_model_config({
        "request_params": {"api_version": "2025-01-01"},
        "extra_body": {"new": True},
        "extra_params": {"legacy": True},
    })

    assert serialized["request_params"] == {"api_version": "2025-01-01"}
    assert serialized["extra_body"] == {"legacy": True, "new": True}
    assert "extra_params" not in serialized


def test_api_schemas_reject_invalid_protocol_and_ranges() -> None:
    with pytest.raises(ValidationError):
        CreateProviderReq(id="provider", api_type="unknown")
    with pytest.raises(ValidationError):
        UpdateModelReq(temperature=3)
    with pytest.raises(ValidationError):
        UpdateModelReq(timeout=0)
    with pytest.raises(ValidationError):
        UpdateModelReq(request_params={"model": "other"})


async def test_sub_agent_partial_update_and_retryable_persistence_error(monkeypatch) -> None:
    from fastapi import HTTPException

    from web.routers import models

    writes: list[dict[str, object]] = []

    def update(_name: str, **fields: object) -> tuple[bool, str]:
        writes.append(fields)
        return True, "ok"

    monkeypatch.setattr(models._svc, "update_sub_agent", update)
    await models.update_sub_agent("helper", models.SubAgentUpdateReq(description=""))
    await models.update_sub_agent("helper", models.SubAgentUpdateReq(output_schema=None))
    assert writes[0]["description"] == "" and writes[0]["output_schema"] is None
    assert writes[1]["description"] is None and writes[1]["output_schema"] == "clear"

    def disk_failure(_name: str, **fields: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(models._svc, "update_sub_agent", disk_failure)
    with pytest.raises(HTTPException) as failed:
        await models.update_sub_agent("helper", models.SubAgentUpdateReq(description="draft"))
    assert failed.value.status_code == 503
