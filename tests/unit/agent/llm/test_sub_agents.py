"""子代理统一注册表（LLMManager.sub_agents）单元测试。

覆盖：内置难度档保护 / legacy delegation_tiers 迁移 / CRUD 与池语义 /
解析回退（池走查 + 降挡）/ 持久化往返 / rename 重映射。
"""

from __future__ import annotations

import json

from agent.llm.llm_manager import LLMManager


def _write_config(path, *, models=None, sub_agents=None, delegation_tiers=None) -> None:
    models = models if models is not None else [
        {"id": "chat-a", "model": "a"},
        {"id": "chat-b", "model": "b"},
        {"id": "chat-c", "model": "c"},
        {"id": "emb", "model": "e", "model_types": ["embedding"]},
    ]
    data = {
        "providers": [{
            "id": "prov",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "api_type": "openai",
            "models": models,
        }],
        "type_priorities": {"chat": ["chat-a", "chat-b", "chat-c"]},
        "default_chat": "chat-a",
    }
    if sub_agents is not None:
        data["sub_agents"] = sub_agents
    if delegation_tiers is not None:
        data["delegation_tiers"] = delegation_tiers
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_manager(tmp_path) -> LLMManager:
    config_path = tmp_path / "llm.json"
    _write_config(config_path)
    return LLMManager(str(config_path))


def _profile(mgr: LLMManager, name: str) -> dict:
    return next(p for p in mgr.list_sub_agents() if p["name"] == name)


class TestBuiltinTiers:
    def test_builtins_always_present(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        names = [p["name"] for p in mgr.list_sub_agents()]
        assert names[:3] == ["easy", "medium", "hard"]
        assert all(p["builtin"] and p["tier"] > 0 for p in mgr.list_sub_agents()[:3])

    def test_legacy_delegation_tiers_migrated(self, tmp_path) -> None:
        config_path = tmp_path / "llm.json"
        _write_config(
            config_path,
            delegation_tiers={"1": ["chat-b"], "2": ["chat-c"], "3": ["chat-a"]},
        )
        mgr = LLMManager(str(config_path))
        assert _profile(mgr, "easy")["models"] == ["chat-b"]
        assert _profile(mgr, "medium")["models"] == ["chat-c"]
        assert _profile(mgr, "hard")["models"] == ["chat-a"]
        # 首次保存后 legacy 键消失，统一格式落盘
        mgr.save_config()
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert "delegation_tiers" not in saved
        assert saved["sub_agents"]["easy"]["models"] == ["chat-b"]

    def test_new_format_takes_precedence_over_legacy(self, tmp_path) -> None:
        config_path = tmp_path / "llm.json"
        _write_config(
            config_path,
            sub_agents={"easy": {"models": ["chat-c"], "description": ""}},
            delegation_tiers={"1": ["chat-b"]},
        )
        mgr = LLMManager(str(config_path))
        assert _profile(mgr, "easy")["models"] == ["chat-c"]

    def test_builtin_names_reserved_and_protected(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        # 不可创建同名自定义档案
        ok, msg = mgr.create_sub_agent("easy", "chat-a")
        assert not ok and "内置" in msg
        # 不可删除内置档
        ok, msg = mgr.remove_sub_agent("hard")
        assert not ok and "不可删除" in msg
        # 可以调整内置档池（等价于原 set_delegation_tier）
        ok, msg = mgr.update_sub_agent("easy", models=["chat-b", "chat-c"])
        assert ok
        assert _profile(mgr, "easy")["models"] == ["chat-b", "chat-c"]

    def test_legacy_single_model_id_migrated(self, tmp_path) -> None:
        config_path = tmp_path / "llm.json"
        _write_config(config_path, sub_agents={
            "researcher": {"model_id": "chat-b", "description": "调研"},
        })
        mgr = LLMManager(str(config_path))
        assert _profile(mgr, "researcher")["models"] == ["chat-b"]


class TestCrud:
    def test_create_and_list(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        ok, msg = mgr.create_sub_agent("researcher", "chat-b", "资料调研")
        assert ok, msg
        profile = _profile(mgr, "researcher")
        assert profile["models"] == ["chat-b"]
        assert profile["description"] == "资料调研"
        assert profile["builtin"] is False
        assert profile["first_available"] == "chat-b"
        assert profile["model_enabled"] is True

    def test_create_duplicate_rejected(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        mgr.create_sub_agent("researcher", "chat-a")
        ok, msg = mgr.create_sub_agent("researcher", "chat-b")
        assert not ok and "已存在" in msg

    def test_create_invalid_name_rejected(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        for bad in ("1abc", "中文", "has space", "a" * 33, "", "a$b"):
            ok, _ = mgr.create_sub_agent(bad, "chat-a")
            assert not ok, bad

    def test_create_missing_or_non_chat_model_rejected(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        assert not mgr.create_sub_agent("r1", "nope")[0]
        assert not mgr.create_sub_agent("r2", "emb")[0]

    def test_update_pool_and_fields(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        mgr.create_sub_agent("researcher", "chat-a", "旧描述")
        # model_id 单模型快捷写法
        ok, _ = mgr.update_sub_agent("researcher", model_id="chat-b")
        assert ok
        profile = _profile(mgr, "researcher")
        assert profile["models"] == ["chat-b"]
        assert profile["description"] == "旧描述"
        # models 列表整体替换（降级链）
        mgr.update_sub_agent("researcher", models=["chat-c", "chat-b"])
        assert _profile(mgr, "researcher")["models"] == ["chat-c", "chat-b"]
        # 池校验：非法模型拒绝且不落盘
        ok, msg = mgr.update_sub_agent("researcher", models=["chat-c", "emb"])
        assert not ok and "embedding" not in msg and "chat 类型" in msg
        assert _profile(mgr, "researcher")["models"] == ["chat-c", "chat-b"]

    def test_update_unknown_rejected(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        assert not mgr.update_sub_agent("ghost")[0]

    def test_remove(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        mgr.create_sub_agent("researcher", "chat-a")
        ok, _ = mgr.remove_sub_agent("researcher")
        assert ok
        assert all(p["name"] != "researcher" for p in mgr.list_sub_agents())
        assert not mgr.remove_sub_agent("researcher")[0]


class TestResolve:
    def test_resolve_pool_walk_falls_through_disabled(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        mgr.update_sub_agent("hard", models=["chat-b", "chat-c"])
        assert mgr.resolve_sub_agent_model("hard") == "chat-b"
        mgr.update_model("chat-b", enabled=False)
        # 池内回退：chat-b 停用 → chat-c
        assert mgr.resolve_sub_agent_model("hard") == "chat-c"

    def test_resolve_unknown_returns_none(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        assert mgr.resolve_sub_agent_model("ghost") is None

    def test_resolve_empty_pool_returns_none(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        assert mgr.resolve_sub_agent_model("easy") is None

    def test_resolve_dangling_reference_falls_back(self, tmp_path) -> None:
        config_path = tmp_path / "llm.json"
        _write_config(config_path, sub_agents={
            "r": {"models": ["gone"], "description": ""},
        })
        mgr = LLMManager(str(config_path))
        profile = _profile(mgr, "r")
        assert profile["model_missing"] is True
        assert profile["first_available"] is None
        assert mgr.resolve_sub_agent_model("r") is None

    def test_resolve_delegation_sugar_and_downgrade(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        # difficulty 是内置档案语法糖：agent_name 与 difficulty 等价
        mgr.update_sub_agent("hard", models=["chat-c"])
        assert mgr.resolve_delegation_model(3) == "chat-c"
        assert mgr.resolve_sub_agent_model("hard") == "chat-c"
        # 本档空 → 降挡：hard 空、medium 有 → 取 medium
        mgr.update_sub_agent("hard", models=[])
        mgr.update_sub_agent("medium", models=["chat-b"])
        assert mgr.resolve_delegation_model(3) == "chat-b"
        # 全部为空 → None（默认模型）
        mgr.update_sub_agent("medium", models=[])
        assert mgr.resolve_delegation_model(3) is None
        # 非法挡位
        assert mgr.resolve_delegation_model(0) is None
        assert mgr.resolve_delegation_model(9) is None


class TestPersistence:
    def test_round_trip(self, tmp_path) -> None:
        config_path = tmp_path / "llm.json"
        _write_config(config_path)
        mgr = LLMManager(str(config_path))
        mgr.update_sub_agent("easy", models=["chat-b"])
        mgr.create_sub_agent("researcher", "chat-c", "调研")

        reloaded = LLMManager(str(config_path))
        assert _profile(reloaded, "easy")["models"] == ["chat-b"]
        assert _profile(reloaded, "researcher")["models"] == ["chat-c"]
        assert _profile(reloaded, "researcher")["description"] == "调研"
        # 内置档（空池）同样持久化
        assert _profile(reloaded, "hard")["models"] == []

    def test_rename_model_remaps_pools(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        mgr.update_sub_agent("easy", models=["chat-b"])
        mgr.create_sub_agent("researcher", "chat-b")
        assert mgr.rename_model("chat-b", "chat-b2") is True
        assert _profile(mgr, "easy")["models"] == ["chat-b2"]
        assert _profile(mgr, "researcher")["models"] == ["chat-b2"]

    def test_remove_model_keeps_dangling_reference(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        mgr.create_sub_agent("researcher", "chat-b")
        mgr.remove_model("chat-b")
        assert _profile(mgr, "researcher")["model_missing"] is True
