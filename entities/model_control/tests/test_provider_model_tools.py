"""供应商/模型注册工具（add/update/remove_provider、add/remove_model、reload_model_config）单元测试。

要点：密钥绝不回显、${ENV_VAR} 引用磁盘保留+内存展开、掩码提交不动现值、
连接变更传播到所属模型、白名单扩展（enabled/model 放行，api_key 仍封禁）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from entities.model_control import tools as mc_tools


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from agent.llm.llm_manager import LLMManager

    cfg = tmp_path / "llm_clients.json"
    cfg.write_text(json.dumps({
        "providers": [], "type_priorities": {}, "default_chat": "",
    }), encoding="utf-8")
    manager = LLMManager(str(cfg))
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    return manager, cfg


def _disk(cfg: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(cfg.read_text(encoding="utf-8"))


class TestAddProvider:
    def test_ok_and_secret_not_echoed(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        result = json.loads(mc_tools.add_provider(
            "prismml", "https://api.prismml.example/v1", api_key="test-fixture-mock-key-001",
        ))
        assert result["ok"] is True
        assert "sk-live-secret-123" not in json.dumps(result, ensure_ascii=False)
        assert result["provider"]["api_key"] != "sk-live-secret-123"
        disk = _disk(cfg)
        assert disk["providers"][0]["id"] == "prismml"
        assert disk["providers"][0]["api_key"] == "sk-live-secret-123"
        assert manager.get_provider("prismml") is not None

    def test_duplicate_rejected(self, env) -> None:  # type: ignore[no-untyped-def]
        mc_tools.add_provider("p1", "http://a")
        result = json.loads(mc_tools.add_provider("p1", "http://b"))
        assert "error" in result and "update_provider" in result["error"]

    def test_invalid_api_type(self, env) -> None:  # type: ignore[no-untyped-def]
        result = json.loads(mc_tools.add_provider("p1", "http://a", api_type="bogus"))
        assert "error" in result

    def test_env_ref_preserved_on_disk_expanded_in_memory(
        self, env, monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        monkeypatch.setenv("TEST_PROVIDER_KEY", "sk-env-real-456")
        manager, cfg = env
        result = json.loads(mc_tools.add_provider(
            "envp", "http://a", api_key="${TEST_PROVIDER_KEY}",
        ))
        assert result["ok"] is True
        assert "sk-env-real-456" not in json.dumps(result, ensure_ascii=False)
        disk = _disk(cfg)
        assert disk["providers"][0]["api_key"] == "${TEST_PROVIDER_KEY}"
        prov = manager.get_provider("envp")
        assert prov is not None and prov.api_key == "sk-env-real-456"

    def test_unset_env_ref_warns_but_registers(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        result = json.loads(mc_tools.add_provider(
            "unsetp", "http://a", api_key="${DEFINITELY_UNSET_VAR_XYZ}",
        ))
        assert result["ok"] is True and result.get("warnings")
        disk = _disk(cfg)
        assert disk["providers"][0]["api_key"] == "${DEFINITELY_UNSET_VAR_XYZ}"
        assert manager.get_provider("unsetp") is not None


class TestUpdateProvider:
    def test_masked_or_empty_key_keeps_current(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, _cfg = env
        mc_tools.add_provider("p1", "http://a", api_key="sk-original-key")
        result = json.loads(mc_tools.update_provider("p1", api_key="sk-o****-key"))
        assert result["ok"] is True
        prov = manager.get_provider("p1")
        assert prov is not None and prov.api_key == "sk-original-key"
        result = json.loads(mc_tools.update_provider("p1"))
        assert "未修改" in result["message"]

    def test_base_url_change_propagates_to_models(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, _cfg = env
        mc_tools.add_provider("p1", "http://a", api_key="k")
        mc_tools.add_model("p1", "m1")
        result = json.loads(mc_tools.update_provider("p1", base_url="http://b"))
        assert result["ok"] is True and "base_url" in result["changed"]
        client = manager.get_client("m1")
        assert client is not None and client.config.base_url == "http://b"

    def test_env_ref_rotation_keeps_ref_on_disk(
        self, env, monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        monkeypatch.setenv("ROTATED_KEY", "sk-rotated-789")
        manager, cfg = env
        mc_tools.add_provider("p1", "http://a", api_key="sk-old-plain")
        mc_tools.add_model("p1", "m1")
        result = json.loads(mc_tools.update_provider("p1", api_key="${ROTATED_KEY}"))
        assert result["ok"] is True and "api_key" in result["changed"]
        disk = _disk(cfg)
        assert disk["providers"][0]["api_key"] == "${ROTATED_KEY}"
        client = manager.get_client("m1")
        assert client is not None and client.config.api_key == "sk-rotated-789"

    def test_unknown_provider(self, env) -> None:  # type: ignore[no-untyped-def]
        result = json.loads(mc_tools.update_provider("ghost", base_url="http://b"))
        assert "error" in result


class TestAddModel:
    def test_ok_and_persisted(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        mc_tools.add_provider("p1", "http://a", api_key="k")
        result = json.loads(mc_tools.add_model(
            "p1", "pm-flash", model="pm-flash-2026",
            supports_reasoning=True, reasoning_effort="high", context_window=200000,
        ))
        assert result["ok"] is True
        client = manager.get_client("pm-flash")
        assert client is not None
        assert client.config.model == "pm-flash-2026"
        assert client.config.reasoning_effort == "high"
        assert client.config.context_window == 200000
        # 空配置下首个 chat 模型自动成为默认
        assert result.get("note")
        disk = _disk(cfg)
        assert disk["providers"][0]["models"][0]["id"] == "pm-flash"
        assert disk["default_chat"] == "pm-flash"

    def test_validation_errors(self, env) -> None:  # type: ignore[no-untyped-def]
        mc_tools.add_provider("p1", "http://a")
        mc_tools.add_model("p1", "m1")
        for kwargs in (
            dict(provider_id="ghost", model_id="x"),
            dict(provider_id="p1", model_id="m1"),
            dict(provider_id="p1", model_id="m2", model_types="chat,bogus"),
            dict(provider_id="p1", model_id="m3", reasoning_effort="extreme"),
            dict(provider_id="p1", model_id="m4", temperature=3.0),
        ):
            result = json.loads(mc_tools.add_model(**kwargs))
            assert "error" in result, kwargs


class TestRemovePaths:
    def test_remove_model_reports_default_switch(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, _cfg = env
        mc_tools.add_provider("p1", "http://a")
        mc_tools.add_model("p1", "m1")
        mc_tools.add_model("p1", "m2")
        manager.set_default("m1")
        result = json.loads(mc_tools.remove_model("m1"))
        assert result["ok"] is True and "note" in result
        assert manager.get_client("m1") is None
        assert manager.default_name != "m1"

    def test_remove_provider_cascades(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, _cfg = env
        mc_tools.add_provider("p1", "http://a")
        mc_tools.add_model("p1", "m1")
        mc_tools.add_model("p1", "m2")
        result = json.loads(mc_tools.remove_provider("p1"))
        assert result["ok"] is True
        assert set(result["removed_models"]) == {"m1", "m2"}
        assert manager.get_provider("p1") is None
        assert manager.get_client("m1") is None

    def test_remove_unknown(self, env) -> None:  # type: ignore[no-untyped-def]
        assert "error" in json.loads(mc_tools.remove_model("ghost"))
        assert "error" in json.loads(mc_tools.remove_provider("ghost"))


class TestUpdateModelConfigWhitelist:
    def test_enabled_and_model_allowed(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, _cfg = env
        mc_tools.add_provider("p1", "http://a")
        mc_tools.add_model("p1", "m1")
        result = json.loads(mc_tools.update_model_config("m1", "enabled", "false"))
        assert result["ok"] is True
        client = manager.get_client("m1")
        assert client is not None and client.config.enabled is False
        result = json.loads(mc_tools.update_model_config("m1", "model", "m1-fixed"))
        assert result["ok"] is True
        assert client.config.model == "m1-fixed"

    def test_connection_fields_still_rejected(self, env) -> None:  # type: ignore[no-untyped-def]
        mc_tools.add_provider("p1", "http://a")
        mc_tools.add_model("p1", "m1")
        for field in ("api_key", "base_url", "model_types"):
            result = json.loads(mc_tools.update_model_config("m1", field, "x"))
            assert "error" in result


class TestReloadAndList:
    def test_reload_tool_returns_summary(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        cfg.write_text(json.dumps({
            "default_chat": "",
            "providers": [{
                "id": "hand", "base_url": "http://h",
                "models": [{"id": "hm", "model": "hm-x"}],
            }],
        }), encoding="utf-8")
        result = json.loads(mc_tools.reload_model_config())
        assert result["ok"] is True
        assert result["providers"]["added"] == ["hand"]
        assert result["models"]["added"] == ["hm"]
        assert manager.get_client("hm") is not None

    def test_reload_tool_corrupt_file(self, env) -> None:  # type: ignore[no-untyped-def]
        _manager, cfg = env
        cfg.write_text("{bad", encoding="utf-8")
        result = json.loads(mc_tools.reload_model_config())
        assert "error" in result

    def test_list_models_includes_masked_providers(self, env) -> None:  # type: ignore[no-untyped-def]
        mc_tools.add_provider("p1", "http://a", api_key="test-fixture-mock-key-002")
        mc_tools.add_model("p1", "m1")
        result = json.loads(mc_tools.list_models())
        providers = result["providers"]
        assert providers[0]["id"] == "p1" and providers[0]["model_count"] == 1
        assert "sk-list-secret-1" not in json.dumps(result, ensure_ascii=False)
