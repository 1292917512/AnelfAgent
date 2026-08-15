"""模型启用开关与子代理统一注册表测试。

覆盖：
- 禁用模型不参与 get_by_type / get_default / fallback / 执行路径查询
- 禁用状态持久化（回读配置生效）
- set_default 拒绝禁用模型
- 子代理注册表：池校验 / resolve 映射 / 传错回默认 / 全禁用降挡
- DelegationManager 难度 → 模型透传
"""

from __future__ import annotations

from agent.llm.config import ModelType
from agent.llm.llm_manager import LLMManager


def _make_manager(tmp_path) -> LLMManager:
    mgr = LLMManager(str(tmp_path / "llm.json"))
    mgr.create_provider("p1")
    # 首个 chat+tools 模型自动成为 default_chat
    mgr.create_model("p1", "cheap", model="cheap-model")
    mgr.create_model("p1", "mid", model="mid-model")
    mgr.create_model("p1", "smart", model="smart-model")
    return mgr


class TestModelEnableSwitch:
    def test_disabled_model_skipped_in_selection(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.update_model("cheap", enabled=False) is True

        # 管理查询仍可见，执行查询返回 None
        assert mgr.get_client("cheap") is not None
        assert mgr.get_enabled_client("cheap") is None

        # 默认/按类型/回退均跳过禁用模型
        assert mgr.get_default().config.name == "mid"
        assert mgr.get_by_type(ModelType.CHAT).config.name == "mid"
        names = [c.config.name for c in mgr.get_fallback_chat_clients()]
        assert "cheap" not in names

    def test_disabled_default_falls_to_next_enabled(self, tmp_path):
        mgr = _make_manager(tmp_path)
        # cheap 是 default_chat；禁用后落下一可用
        assert mgr.get_default().config.name == "cheap"
        mgr.update_model("cheap", enabled=False)
        assert mgr.get_default().config.name == "mid"

    def test_set_default_rejects_disabled(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.update_model("smart", enabled=False)
        assert mgr.set_default("smart") is False

    def test_enabled_persisted_to_config(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.update_model("mid", enabled=False)

        reloaded = LLMManager(str(tmp_path / "llm.json"))
        assert reloaded.get_client("mid").config.enabled is False
        assert reloaded.get_client("cheap").config.enabled is True

    def test_priorities_expose_enabled(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.update_model("mid", enabled=False)
        chat_items = mgr.get_type_priorities()["chat"]
        by_id = {item["id"]: item for item in chat_items}
        assert by_id["mid"]["enabled"] is False
        assert by_id["cheap"]["enabled"] is True


class TestSubAgentRegistry:
    """内置难度档（统一注册表）的池校验 / 映射 / 降挡 / 持久化。"""

    def _with_tiers(self, tmp_path) -> LLMManager:
        mgr = _make_manager(tmp_path)
        assert mgr.update_sub_agent("easy", models=["cheap"])[0]
        assert mgr.update_sub_agent("medium", models=["mid"])[0]
        assert mgr.update_sub_agent("hard", models=["smart"])[0]
        return mgr

    def test_resolve_maps_difficulty_to_tier(self, tmp_path):
        mgr = self._with_tiers(tmp_path)
        assert mgr.resolve_delegation_model(1) == "cheap"
        assert mgr.resolve_delegation_model(2) == "mid"
        assert mgr.resolve_delegation_model(3) == "smart"

    def test_resolve_invalid_difficulty_returns_none(self, tmp_path):
        mgr = self._with_tiers(tmp_path)
        assert mgr.resolve_delegation_model(0) is None
        assert mgr.resolve_delegation_model(None) is None
        assert mgr.resolve_delegation_model("x") is None
        assert mgr.resolve_delegation_model(9) is None

    def test_resolve_empty_pool_returns_none(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.resolve_delegation_model(2) is None

    def test_resolve_falls_back_when_tier_disabled(self, tmp_path):
        mgr = self._with_tiers(tmp_path)
        mgr.update_model("smart", enabled=False)
        # 挡位 3 全禁用 → 降挡到 2
        assert mgr.resolve_delegation_model(3) == "mid"

    def test_update_rejects_unknown_builtin_and_non_chat(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.create_model("p1", "emb", model="e", model_types=["embedding"])
        # 非内置档案名不存在
        assert mgr.update_sub_agent("tier4", models=["cheap"])[0] is False
        # 非 chat 模型拒绝（整池不落盘）
        ok, msg = mgr.update_sub_agent("easy", models=["emb", "cheap"])
        assert not ok and "chat 类型" in msg
        # 悬空模型引用同样拒绝（写入路径强校验）
        assert mgr.update_sub_agent("easy", models=["cheap", "nope"])[0] is False

    def test_tiers_persisted_to_config(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.update_sub_agent("medium", models=["mid"])

        reloaded = LLMManager(str(tmp_path / "llm.json"))
        profiles = {p["name"]: p for p in reloaded.list_sub_agents()}
        assert profiles["medium"]["models"] == ["mid"]
        assert profiles["easy"]["models"] == []


class TestDelegationDifficultyWiring:
    def test_manager_resolves_model_for_difficulty(self, tmp_path):
        from agent.delegation.delegation_manager import DelegationManager

        class _StubMind:
            llm_manager = _make_manager(tmp_path)

        mgr = DelegationManager(_StubMind())  # type: ignore[arg-type]
        mgr._mind.llm_manager.update_sub_agent("easy", models=["cheap"])

        assert mgr._resolve_model("", 0) == ""
        assert mgr._resolve_model("", 1) == "cheap"
        assert mgr._resolve_model("", 9) == ""
