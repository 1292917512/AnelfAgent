"""Minecraft 安装升级沿用连接参数，显式选项才覆盖已保存值。"""

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from core.plugins.manager import PluginManager
from entities.mcp.config import MCPServerStore
from scripts import setup_minecraft


@pytest.mark.parametrize("upgrade", [False, True])
def test_setup_preserves_connection_and_player_settings(
    manager: PluginManager,
    plugin_env: Any,
    monkeypatch: pytest.MonkeyPatch,
    upgrade: bool,
) -> None:
    pkg = plugin_env.make_plugin("minecraft-companion", tools=False, skill=False)
    manager.install_from_source(str(pkg))
    name = "minecraft-companion_srv"
    store = MCPServerStore()
    store.update_server_config(
        name,
        {
            "env": {
                "MCP_DEFAULT_HOST": "192.168.1.10",
                "MCP_DEFAULT_PORT": "54321",
                "MCP_DEFAULT_USERNAME": "bot@example.com",
                "MCP_DEFAULT_AUTH": "microsoft",
                "MCP_DEFAULT_VERSION": "26.1",
                "MCP_DISABLE_GROUPS": "raw",
            }
        },
    )
    (pkg / "change.txt").write_text("upgrade payload", encoding="utf-8")
    saved = {"minecraft_server_id": "friends", "minecraft_allowed_players": ["Alice"]}
    write_channel = Mock()
    monkeypatch.setattr("core.config.ConfigManager.initialize", lambda: None)
    monkeypatch.setattr("core.config.ConfigManager.get", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr("core.path.workspace_root", lambda: str(plugin_env.root / "workspace"))
    monkeypatch.setattr("core.plugins.get_plugin_manager", lambda: manager)
    monkeypatch.setattr("agent.channel.config.register_channel_schema", lambda name: True)
    monkeypatch.setattr("agent.channel.config.set_channel_config", write_channel)
    monkeypatch.setattr(setup_minecraft, "node_runtime", lambda *args: (Path("node"), Path("npm-cli.js")))
    monkeypatch.setattr(setup_minecraft.subprocess, "run", Mock())
    monkeypatch.setattr(setup_minecraft, "ensure_worker_profile", lambda: "exists")
    args = argparse.Namespace(
        host=None,
        port=54322,
        username=None,
        auth=None,
        version=None,
        world_id=None,
        player=None,
        clear_players=False,
        node=None,
        npm_cli="",
        download_node=False,
        upgrade=upgrade,
    )

    setup_minecraft.setup(args)

    current = store.get_server_config(name)
    assert current is not None
    assert current["env"]["MCP_DEFAULT_HOST"] == "192.168.1.10"
    assert current["env"]["MCP_DEFAULT_PORT"] == "54322"
    assert current["env"]["MCP_DEFAULT_USERNAME"] == "bot@example.com"
    assert current["env"]["MCP_DEFAULT_AUTH"] == "microsoft"
    assert current["env"]["MCP_DISABLE_GROUPS"] == "raw"
    assert write_channel.call_args.kwargs["server_id"] == "friends"
    assert write_channel.call_args.kwargs["allowed_players"] == ["Alice"]


class _FakeLLMManager:
    """只覆盖 ensure_worker_profile 所需的最小 LLMManager 面。"""

    def __init__(self, chat_models: list[str], default: str = "") -> None:
        self._chat_models = set(chat_models)
        self._default = default
        self.created: dict[str, Any] = {}

    def get_sub_agent_profile(self, name: str) -> Any:
        return self.created.get(name)

    def _validate_sub_agent_model(self, model_id: str) -> Any:
        if model_id in self._chat_models:
            return None
        return f"模型 '{model_id}' 不存在"

    @property
    def default_name(self) -> str:
        return self._default

    def create_sub_agent(self, **kwargs: Any) -> tuple[bool, str]:
        if self._validate_sub_agent_model(kwargs["model_id"]) is not None:
            return False, "模型不存在"
        self.created[kwargs["name"]] = kwargs
        return True, "ok"

    def update_sub_agent(self, name: str, **kwargs: Any) -> tuple[bool, str]:
        self.created[name].update(kwargs)
        return True, "ok"


def test_ensure_worker_profile_creates_pool_with_available_fast_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLLMManager(chat_models=["glm-4.5-air", "minimax-m2", "gpt-4o"])
    monkeypatch.setattr("agent.llm.llm_manager.get_llm_manager", lambda: fake)

    assert setup_minecraft.ensure_worker_profile() == "created"

    profile = fake.created["mc-worker"]
    assert profile["model_id"] == "glm-4.5-air"
    assert profile["tool_tags"] == ["mcp:minecraft"]
    assert profile["output_schema"]["required"] == ["done"]
    assert fake.created["mc-worker"]["models"] == ["glm-4.5-air", "minimax-m2"]


def test_ensure_worker_profile_falls_back_to_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLLMManager(chat_models=["gpt-4o"], default="gpt-4o")
    monkeypatch.setattr("agent.llm.llm_manager.get_llm_manager", lambda: fake)

    assert setup_minecraft.ensure_worker_profile() == "created"
    assert fake.created["mc-worker"]["model_id"] == "gpt-4o"


def test_ensure_worker_profile_skips_without_any_chat_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeLLMManager(chat_models=[], default="")
    monkeypatch.setattr("agent.llm.llm_manager.get_llm_manager", lambda: fake)

    assert setup_minecraft.ensure_worker_profile() == "skipped"
    assert "跳过 mc-worker" in capsys.readouterr().out
    assert fake.created == {}


def test_ensure_worker_profile_keeps_existing_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLLMManager(chat_models=["glm-4.5-air"])
    fake.created["mc-worker"] = {"name": "mc-worker", "model_id": "custom"}
    monkeypatch.setattr("agent.llm.llm_manager.get_llm_manager", lambda: fake)

    assert setup_minecraft.ensure_worker_profile() == "exists"
    assert fake.created["mc-worker"]["model_id"] == "custom"
