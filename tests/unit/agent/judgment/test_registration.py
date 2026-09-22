"""judgment 模块注册链路守卫：配置面 / 分组排序 / 工具组激活 / 工具错误契约。"""

from __future__ import annotations

import json

import pytest

EXPECTED_CONFIG_KEYS = {
    "judgment_enabled",
    "judgment_api_key",
    "judgment_base_url",
    "judgment_model",
    "judgment_timeout",
    "judgment_fallback_enabled",
    "judgment_fallback_model",
    "judgment_fallback_effort",
    "judgment_context_messages",
    "judgment_context_max_chars",
    "judgment_full_max_chars",
}


class TestConfigRegistration:
    def test_group_registered_with_all_keys(self) -> None:
        import agent.judgment  # noqa: F401
        from core.config import ConfigRegistry

        groups = ConfigRegistry.get_grouped_items()
        assert "judgment/core" in groups
        keys = {item.key for item in groups["judgment/core"]}
        assert keys == EXPECTED_CONFIG_KEYS

    def test_api_key_is_password_masked(self) -> None:
        import agent.judgment  # noqa: F401
        from core.config import ConfigRegistry

        item = ConfigRegistry.get_item("judgment_api_key")
        assert item is not None and item.is_secret


class TestGroupOrder:
    def test_judgment_group_order(self) -> None:
        import agent.judgment  # noqa: F401
        from core.entity import EntityRegistry

        assert EntityRegistry.group_sort_key("judgment") == (2, "judgment")


class TestToolRegistration:
    def test_activation_registers_judge(self) -> None:
        """judge 工具经 deferred 注册表激活（共享会话幂等：已弹出则注册表已有）。"""
        import agent.judgment.tools  # noqa: F401
        from core.entity import EntityRegistry
        from entities._sdk import _deferred_registry, activate_group

        activate_group("judgment", "判断")
        assert "judge" in EntityRegistry.get_all_names()
        assert not _deferred_registry.get("judgment")

    def test_judge_schema_carries_questions_items(self) -> None:
        import agent.judgment.tools  # noqa: F401
        from core.entity import EntityRegistry
        from entities._sdk import activate_group

        activate_group("judgment", "判断")
        schemas = EntityRegistry.get_tool_schemas_by_group("judgment")
        judge_schema = next(
            s for s in schemas if s.get("function", {}).get("name") == "judge"
        )
        props = judge_schema["function"]["parameters"]["properties"]
        assert props["questions"]["type"] == "array"
        item_props = props["questions"]["items"]["properties"]
        assert item_props["type"]["enum"] == ["choice", "score", "noul"]
        assert props["context_mode"]["enum"] == ["none", "conversation", "full"]
        # state/context_mode 均为可选（默认 none 不注入）
        required = judge_schema["function"]["parameters"].get("required", [])
        assert required == ["questions"]


class TestToolErrorContract:
    async def test_bad_questions_param_error(self) -> None:
        import agent.judgment.tools as tools

        result = json.loads(await tools.judge(state="文本", questions=[]))
        assert result["error"] and result["cause"] == "param"

    async def test_disabled_state_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent.judgment.tools as tools
        from agent.judgment import engine as engine_mod
        from agent.judgment.engine import JudgmentConfig

        monkeypatch.setattr(
            engine_mod,
            "load_config",
            lambda: JudgmentConfig(
                enabled=False, api_key="", base_url="", model="", timeout=30.0,
                fallback_enabled=True, fallback_model="", fallback_effort="low",
            ),
        )
        result = json.loads(await tools.judge(
            state="文本",
            questions=[{"id": "q", "type": "noul", "instructions": "成立吗？"}],
        ))
        assert result["cause"] == "state"
