"""模型配置动态修复（update_model_config + 运行时问题可见化）单元测试。"""

from __future__ import annotations

import json

from agent.llm.llm_client import LLMClient, LLMClientConfig
from entities.model_control.tools import _parse_field_value


def _client(**overrides) -> LLMClient:  # type: ignore[no-untyped-def]
    return LLMClient(LLMClientConfig(model="m", api_type="openai", **overrides))


class TestGetRuntimeIssues:
    def test_no_issues_by_default(self) -> None:
        assert _client().get_runtime_issues() == []

    def test_tool_choice_issue(self) -> None:
        client = _client()
        client._learned_no_forced_tool_choice = True
        issues = client.get_runtime_issues()
        assert len(issues) == 1
        assert "tool_choice" in issues[0]
        assert "update_model_config" in issues[0]

    def test_output_cap_issue(self) -> None:
        client = _client()
        client._learned_output_cap = 4096
        issues = client.get_runtime_issues()
        assert len(issues) == 1
        assert "4096" in issues[0]

    def test_multiple_issues(self) -> None:
        client = _client()
        client._learned_no_forced_tool_choice = True
        client._learned_output_cap = 2048
        assert len(client.get_runtime_issues()) == 2


class TestParseFieldValue:
    def test_timeout_float(self) -> None:
        assert _parse_field_value("timeout", "60") == (60.0, "")
        assert _parse_field_value("timeout", "45.5") == (45.5, "")

    def test_max_tokens_int(self) -> None:
        assert _parse_field_value("max_tokens", "8192") == (8192, "")

    def test_bool_variants(self) -> None:
        assert _parse_field_value("supports_forced_tool_choice", "true") == (True, "")
        assert _parse_field_value("supports_reasoning", "FALSE") == (False, "")

    def test_invalid_bool(self) -> None:
        _, err = _parse_field_value("supports_reasoning", "maybe")
        assert err

    def test_invalid_number(self) -> None:
        _, err = _parse_field_value("timeout", "abc")
        assert err

    def test_non_positive_rejected(self) -> None:
        _, err = _parse_field_value("timeout", "-5")
        assert err
        _, err = _parse_field_value("max_tokens", "0")
        assert err


class TestUpdateModelConfigTool:
    """工具级集成：经 LLMManager 持久化并立即生效。"""

    def test_update_persists_and_takes_effect(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from agent.llm.llm_manager import LLMManager
        from entities.model_control import tools as mc_tools

        config_file = tmp_path / "llm_clients.json"
        config_file.write_text(json.dumps({
            "default_chat": "m1",
            "providers": [{
                "id": "p1", "api_type": "openai", "base_url": "http://x", "api_key": "k",
                "models": [{"id": "m1", "model": "m1", "supports_forced_tool_choice": True}],
            }],
        }), encoding="utf-8")

        manager = LLMManager(str(config_file))
        monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)

        result = json.loads(mc_tools.update_model_config("m1", "supports_forced_tool_choice", "false"))
        assert result["ok"] is True
        assert result["old"] is True and result["new"] is False
        assert manager.get_client("m1").config.supports_forced_tool_choice is False  # type: ignore[union-attr]

        # 持久化到配置文件
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        model_cfg = saved["providers"][0]["models"][0]
        assert model_cfg["supports_forced_tool_choice"] is False

    def test_unknown_model(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from agent.llm.llm_manager import LLMManager
        from entities.model_control import tools as mc_tools

        config_file = tmp_path / "llm_clients.json"
        config_file.write_text(json.dumps({"providers": []}), encoding="utf-8")
        monkeypatch.setattr(
            "agent.llm.get_llm_manager",
            lambda: LLMManager(str(config_file)),
        )
        result = json.loads(mc_tools.update_model_config("ghost", "timeout", "60"))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_field_whitelist(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from agent.llm.llm_manager import LLMManager
        from entities.model_control import tools as mc_tools

        config_file = tmp_path / "llm_clients.json"
        config_file.write_text(json.dumps({
            "providers": [{
                "id": "p1", "api_type": "openai", "base_url": "http://x", "api_key": "k",
                "models": [{"id": "m1", "model": "m1"}],
            }],
        }), encoding="utf-8")
        monkeypatch.setattr(
            "agent.llm.get_llm_manager",
            lambda: LLMManager(str(config_file)),
        )
        for field in ("api_key", "base_url", "model", "model_types"):
            result = json.loads(mc_tools.update_model_config("m1", field, "x"))
            assert result["ok"] is False
            assert "不允许修改" in result["error"]
