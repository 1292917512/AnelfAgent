"""Minecraft 安装升级沿用连接参数，显式选项才覆盖已保存值。"""

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from channels.minecraft.scripts import (
    minecraft_actions,
    minecraft_crafting,
    minecraft_digging,
    minecraft_mining,
    minecraft_placement,
    setup_minecraft,
)
from core.plugins.manager import PluginManager
from entities.mcp.config import MCPServerStore


def test_installed_minecraft_retains_runtime_settings_on_restart(
    manager: PluginManager, plugin_env: Any,
) -> None:
    from core.plugins.store import plugin_payload_dir
    from entities.plugins.activation import activate_plugin

    record = manager.install_from_source(str(setup_minecraft._SOURCE))
    store = MCPServerStore()
    name = record.mcp_servers[0]
    settings: dict[str, Any] = {
        "command": "C:/portable/node.exe", "enabled": True, "priority": "below_normal", "stay_awake": True,
        "env": {
            "MCP_DEFAULT_HOST": "192.168.1.10", "MCP_DEFAULT_PORT": "54321",
            "MCP_DEFAULT_AUTH": "microsoft", "AWESOME_MINEFLAYER_MCP_HOME": "C:/persistent/minecraft",
        },
    }
    store.update_server_config(name, settings)
    for _ in range(2):
        record = activate_plugin(record, plugin_payload_dir(record.name))
        current = store.get_server_config(name)
        assert current is not None
        for key in ("command", "enabled", "priority", "stay_awake"):
            assert current[key] == settings[key]
        for key, value in settings["env"].items():
            assert current["env"][key] == value
        assert current["env"]["MCP_AUTO_CONNECT"] == "false"
    store.set_server_enabled(name, False)
    activate_plugin(record, plugin_payload_dir(record.name))
    current = store.get_server_config(name)
    assert current is not None and current["enabled"] is False


@pytest.mark.parametrize("upgrade", [False, True])
@pytest.mark.parametrize("relocated", [False, True])
def test_setup_preserves_connection_and_player_settings(
    manager: PluginManager,
    plugin_env: Any,
    monkeypatch: pytest.MonkeyPatch,
    upgrade: bool,
    relocated: bool,
) -> None:
    pkg = plugin_env.make_plugin("minecraft-companion", tools=False, skill=False)
    record = manager.install_from_source(str(pkg))
    if relocated:
        legacy_source = plugin_env.root / "old-repo" / "plugins" / "minecraft"
        record.source = str(legacy_source)
        manager.registry.upsert(record)
        monkeypatch.setattr(setup_minecraft, "_LEGACY_SOURCE", legacy_source)
        monkeypatch.setattr(setup_minecraft, "_SOURCE", pkg)
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
    monkeypatch.setattr(setup_minecraft, "patch_executor_view_distance", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_executor_chat_discipline", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_pathfinder_think_timeout", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_craft_count_semantics", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_crafting", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_placement", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_digging", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_mining", lambda payload: "patched")
    monkeypatch.setattr(setup_minecraft, "patch_actions", lambda payload: "patched")
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
    installed = manager.get_plugin("minecraft-companion")
    assert installed is not None and Path(installed.source) == pkg


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
    fake = _FakeLLMManager(chat_models=["minimax-m3", "minimax-m2", "glm-4.5-air"])
    monkeypatch.setattr("agent.llm.llm_manager.get_llm_manager", lambda: fake)

    assert setup_minecraft.ensure_worker_profile() == "created"

    profile = fake.created["mc-worker"]
    assert profile["model_id"] == "minimax-m3"
    assert profile["tool_tags"] == ["mcp:minecraft"]
    assert profile["output_schema"]["required"] == ["done"]
    assert fake.created["mc-worker"]["models"] == ["minimax-m3", "minimax-m2"]


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


def _write_fake_lifecycle(tmp_path, source: str):
    target = tmp_path / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools"
    target.mkdir(parents=True)
    (target / "lifecycle.js").write_text(source, encoding="utf-8")
    return tmp_path


def test_patch_view_distance_applies_default(tmp_path) -> None:
    payload = _write_fake_lifecycle(
        tmp_path, f"const x = {setup_minecraft._VIEW_DISTANCE_ORIGINAL}\n"
    )
    assert setup_minecraft.patch_executor_view_distance(payload) == "patched"
    text = (payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "lifecycle.js").read_text(encoding="utf-8")
    assert setup_minecraft._VIEW_DISTANCE_PATCHED in text
    # 幂等：再打一次不重复替换
    assert setup_minecraft.patch_executor_view_distance(payload) == "exists"


def test_patch_view_distance_fails_on_version_drift(tmp_path) -> None:
    payload = _write_fake_lifecycle(tmp_path, "viewDistance: z.string(),\n")
    with pytest.raises(RuntimeError, match="viewDistance"):
        setup_minecraft.patch_executor_view_distance(payload)


def _write_fake_chat(tmp_path, source: str):
    target = tmp_path / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools"
    target.mkdir(parents=True)
    (target / "chat.js").write_text(source, encoding="utf-8")
    return tmp_path


def test_patch_chat_discipline_applies_turn_limit(tmp_path) -> None:
    payload = _write_fake_chat(tmp_path, f"const x = {setup_minecraft._CHAT_DESC_ORIGINAL}\n")
    assert setup_minecraft.patch_executor_chat_discipline(payload) == "patched"
    text = (payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "chat.js").read_text(encoding="utf-8")
    assert setup_minecraft._CHAT_DESC_PATCHED in text
    assert "never call it more than twice in a row" in text
    # 幂等：再打一次不重复替换
    assert setup_minecraft.patch_executor_chat_discipline(payload) == "exists"


def test_patch_chat_discipline_migrates_v1_to_v2(tmp_path) -> None:
    """v1 措辞有结构性漏洞（工具调用各成一轮，"每回合"无从计数），已打 v1 的环境升级到 v2。"""
    payload = _write_fake_chat(tmp_path, f"const x = {setup_minecraft._CHAT_DESC_PATCHED_V1}\n")
    assert setup_minecraft.patch_executor_chat_discipline(payload) == "patched"
    text = (payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "chat.js").read_text(encoding="utf-8")
    assert setup_minecraft._CHAT_DESC_PATCHED in text
    assert setup_minecraft._CHAT_DESC_PATCHED_V1 not in text


def test_patch_chat_discipline_repairs_broken_v2(tmp_path) -> None:
    """v2 初版内嵌引号未转义（JS 字符串被截断、服务启动 SyntaxError），破损文件就地修复。"""
    broken = setup_minecraft._CHAT_DESC_PATCHED.replace(" (separate items with a semicolon)", setup_minecraft._CHAT_DESC_BROKEN_V2)
    payload = _write_fake_chat(tmp_path, f"const x = {broken}\n")
    assert setup_minecraft.patch_executor_chat_discipline(payload) == "repaired"
    text = (payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "chat.js").read_text(encoding="utf-8")
    assert setup_minecraft._CHAT_DESC_PATCHED in text
    assert setup_minecraft._CHAT_DESC_BROKEN_V2 not in text


def test_patch_chat_discipline_fails_on_version_drift(tmp_path) -> None:
    payload = _write_fake_chat(tmp_path, 'description: "Chat with the server.",\n')
    with pytest.raises(RuntimeError, match="chat"):
        setup_minecraft.patch_executor_chat_discipline(payload)


def _write_fake_package_file(tmp_path: Path, package: str, rel: str, source: str):
    target = tmp_path / "node_modules" / package / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return tmp_path


def test_patch_think_timeout_extends_search_budget(tmp_path) -> None:
    payload = _write_fake_package_file(
        tmp_path, "mineflayer-pathfinder", "index.js", f"{setup_minecraft._PATHFINDER_TIMEOUT_ORIGINAL}\n"
    )
    assert setup_minecraft.patch_pathfinder_think_timeout(payload) == "patched"
    text = (payload / "node_modules" / "mineflayer-pathfinder" / "index.js").read_text(encoding="utf-8")
    assert setup_minecraft._PATHFINDER_TIMEOUT_PATCHED in text
    assert setup_minecraft.patch_pathfinder_think_timeout(payload) == "exists"


def test_patch_think_timeout_fails_on_version_drift(tmp_path) -> None:
    payload = _write_fake_package_file(tmp_path, "mineflayer-pathfinder", "index.js", "bot.pathfinder.thinkTimeout = 9999\n")
    with pytest.raises(RuntimeError, match="thinkTimeout"):
        setup_minecraft.patch_pathfinder_think_timeout(payload)


def test_patch_craft_count_explains_operation_semantics(tmp_path) -> None:
    payload = _write_fake_package_file(
        tmp_path, "awesome-mineflayer-mcp", "dist/tools/crafting.js", f"const x = {setup_minecraft._CRAFT_COUNT_DESC_ORIGINAL}\n"
    )
    assert setup_minecraft.patch_craft_count_semantics(payload) == "patched"
    text = (payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "crafting.js").read_text(encoding="utf-8")
    assert "operations" in text
    assert setup_minecraft.patch_craft_count_semantics(payload) == "exists"


def test_patch_craft_count_fails_on_version_drift(tmp_path) -> None:
    payload = _write_fake_package_file(tmp_path, "awesome-mineflayer-mcp", "dist/tools/crafting.js", "count: z.number(),\n")
    with pytest.raises(RuntimeError, match="craft count"):
        setup_minecraft.patch_craft_count_semantics(payload)


def _crafting_payload(root: Path) -> list[Path]:
    fixtures = {
        "mineflayer/lib/plugins/inventory.js": (
            minecraft_crafting._WAIT_ORIGINAL + "\n  async function syncWindow (window) {"
            + minecraft_crafting._OVERFLOW_ORIGINAL + minecraft_crafting._TOSS_ORIGINAL
        ),
        "mineflayer/lib/plugins/craft.js": "\n".join((
            minecraft_crafting._FINISH_ORIGINAL, minecraft_crafting._CATCH_ORIGINAL, minecraft_crafting._CRAFT_ORIGINAL,
            *(original for original, _ in minecraft_crafting._CRAFT_CHECKPOINTS),
        )),
        "awesome-mineflayer-mcp/dist/bot/state.js": minecraft_crafting._STATE_ORIGINAL,
        "awesome-mineflayer-mcp/dist/tools/crafting.js": "\n".join((
            minecraft_crafting._PREFLIGHT_ORIGINAL,
            minecraft_crafting._COUNT_ORIGINAL,
            minecraft_crafting._ERROR_ORIGINAL,
        )),
        "awesome-mineflayer-mcp/dist/bot/manager.js": "\n".join((
            minecraft_crafting._MANAGER_IMPORT_ORIGINAL, minecraft_crafting._DISCONNECT_ORIGINAL,
            minecraft_crafting._REQUIRE_BOT_ORIGINAL,
            minecraft_crafting._DISCONNECT_RESULT_ORIGINAL, minecraft_crafting._SHUTDOWN_ORIGINAL,
        )),
        "awesome-mineflayer-mcp/dist/index.js": "\n".join((
            minecraft_crafting._EXIT_ORIGINAL, minecraft_crafting._AWAIT_EXIT_ORIGINAL, minecraft_crafting._EOF_ORIGINAL,
        )),
    }
    paths = []
    for name, source in fixtures.items():
        target = root / "node_modules" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        paths.append(target)
    return paths


def test_crafting_patch_is_idempotent(tmp_path: Path) -> None:
    paths = _crafting_payload(tmp_path)
    assert minecraft_crafting.patch_crafting(tmp_path) == "patched"
    helper = tmp_path / "node_modules/mineflayer/lib/anelf_inventory.js"
    assert helper.read_text(encoding="utf-8") == minecraft_crafting._HELPER.read_text(encoding="utf-8")
    expected = [path.read_bytes() for path in paths]
    assert minecraft_crafting.patch_crafting(tmp_path) == "exists"
    assert [path.read_bytes() for path in paths] == expected


@pytest.mark.parametrize("mismatch", ["missing", "ambiguous"])
def test_crafting_patch_validates_all_files_before_writing(tmp_path: Path, mismatch: str) -> None:
    paths = _crafting_payload(tmp_path)
    source = paths[-1].read_text(encoding="utf-8")
    needle = minecraft_crafting._AWAIT_EXIT_ORIGINAL
    paths[-1].write_text(source.replace(needle, "" if mismatch == "missing" else needle * 2), encoding="utf-8")
    before = [path.read_bytes() for path in paths]
    with pytest.raises(RuntimeError, match="合成补丁位置"):
        minecraft_crafting.patch_crafting(tmp_path)
    assert [path.read_bytes() for path in paths] == before
    assert not (tmp_path / "node_modules/mineflayer/lib/anelf_inventory.js").exists()


def test_crafting_patch_requires_window_sync_interface(tmp_path: Path) -> None:
    paths = _crafting_payload(tmp_path)
    paths[0].write_text(minecraft_crafting._WAIT_ORIGINAL, encoding="utf-8")
    with pytest.raises(RuntimeError, match="合成窗口同步接口"):
        minecraft_crafting.patch_crafting(tmp_path)


def test_placement_patch_is_idempotent(tmp_path: Path) -> None:
    source = minecraft_placement._DESCRIPTION_ORIGINAL + "\n" + minecraft_placement._HANDLER_ORIGINAL
    _write_fake_package_file(tmp_path, "awesome-mineflayer-mcp", "dist/tools/digging.js", source)
    assert minecraft_placement.patch_placement(tmp_path) == "patched"
    target = tmp_path / "node_modules/awesome-mineflayer-mcp/dist/tools/digging.js"
    expected = target.read_bytes()
    assert minecraft_placement.patch_placement(tmp_path) == "exists"
    assert target.read_bytes() == expected


@pytest.mark.parametrize("handler", ["", minecraft_placement._HANDLER_ORIGINAL * 2])
def test_placement_patch_refuses_missing_or_ambiguous_handler(tmp_path: Path, handler: str) -> None:
    source = minecraft_placement._DESCRIPTION_ORIGINAL + "\n" + handler
    _write_fake_package_file(tmp_path, "awesome-mineflayer-mcp", "dist/tools/digging.js", source)
    target = tmp_path / "node_modules/awesome-mineflayer-mcp/dist/tools/digging.js"
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="放置补丁位置"):
        minecraft_placement.patch_placement(tmp_path)
    assert target.read_bytes() == before


def _digging_payload(root: Path) -> list[Path]:
    paths: list[Path] = []
    for relative, replacements in minecraft_digging._PATCHES.items():
        target = root / "node_modules" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(original for original, _ in replacements), encoding="utf-8")
        paths.append(target)
    return paths


def test_digging_patch_is_idempotent(tmp_path: Path) -> None:
    paths = _digging_payload(tmp_path)
    assert minecraft_digging.patch_digging(tmp_path) == "patched"
    expected = [path.read_bytes() for path in paths]
    assert minecraft_digging.patch_digging(tmp_path) == "exists"
    assert [path.read_bytes() for path in paths] == expected


@pytest.mark.parametrize("mismatch", ["missing", "ambiguous"])
def test_digging_patch_validates_all_files_before_writing(tmp_path: Path, mismatch: str) -> None:
    paths = _digging_payload(tmp_path)
    source = paths[-1].read_text(encoding="utf-8")
    needle = minecraft_digging._HANDLER_ORIGINAL
    paths[-1].write_text(source.replace(needle, "" if mismatch == "missing" else needle * 2), encoding="utf-8")
    before = [path.read_bytes() for path in paths]
    with pytest.raises(RuntimeError, match="挖掘补丁位置"):
        minecraft_digging.patch_digging(tmp_path)
    assert [path.read_bytes() for path in paths] == before


@pytest.mark.parametrize("drift", [False, True])
def test_mining_patch_installs_idempotently_or_rejects_all_on_drift(tmp_path: Path, drift: bool) -> None:
    paths: list[Path] = []
    for relative, replacements in minecraft_mining._PATCHES.items():
        target = tmp_path / "node_modules" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(original for original, _ in replacements), encoding="utf-8")
        paths.append(target)
    if drift:
        paths[-1].write_text("upstream changed", encoding="utf-8")
        before = [p.read_bytes() for p in paths]
        with pytest.raises(RuntimeError, match="矿洞补丁位置"):
            minecraft_mining.patch_mining(tmp_path)
        assert [p.read_bytes() for p in paths] == before
        assert not (tmp_path / "node_modules/mineflayer/lib/anelf_mining").exists()
    else:
        assert minecraft_mining.patch_mining(tmp_path) == "patched"
        assert minecraft_mining.patch_mining(tmp_path) == "exists"
        helper = tmp_path / "node_modules/mineflayer/lib/anelf_mining/mining-task.cjs"
        assert helper.read_text(encoding="utf-8") == (minecraft_mining._RUNTIME / "mining-task.cjs").read_text(encoding="utf-8")


@pytest.mark.parametrize("drift", [False, True])
def test_actions_patch_validates_before_writing_and_preserves_mining_idempotency(tmp_path: Path, drift: bool) -> None:
    for module in (minecraft_mining, minecraft_actions):
        for relative, replacements in module._PATCHES.items():
            target = tmp_path / "node_modules" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = target.read_text(encoding="utf-8") if target.exists() else ""
            for original, _ in replacements:
                # Some action anchors are introduced by the mining patch itself.
                introduced_by_mining = module is minecraft_actions and any(
                    original in patched for patches in minecraft_mining._PATCHES.values() for _, patched in patches
                )
                if original not in source and not introduced_by_mining:
                    source += original + "\n"
            target.write_text(source, encoding="utf-8")
    minecraft_mining.patch_mining(tmp_path)
    if drift:
        (tmp_path / "node_modules/awesome-mineflayer-mcp/dist/bot/manager.js").write_text("drift", encoding="utf-8")
    paths = list((tmp_path / "node_modules").rglob("*.js"))
    before = {path: path.read_bytes() for path in paths}
    if drift:
        with pytest.raises(RuntimeError, match="动作控制补丁"):
            minecraft_actions.patch_actions(tmp_path)
        assert {path: path.read_bytes() for path in paths} == before
        assert not (tmp_path / "node_modules/awesome-mineflayer-mcp/dist/bot/anelf-actions.mjs").exists()
    else:
        assert minecraft_actions.patch_actions(tmp_path) == "patched"
        assert minecraft_mining.patch_mining(tmp_path) == "exists"
        assert minecraft_actions.patch_actions(tmp_path) == "exists"
