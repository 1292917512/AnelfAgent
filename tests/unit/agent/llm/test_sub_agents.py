"""子代理统一注册表（LLMManager.sub_agents）单元测试。

覆盖：内置难度档保护 / legacy delegation_tiers 迁移 / CRUD 与池语义 /
解析回退（池走查 + 降挡）/ 持久化往返 / rename 重映射。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest

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


@pytest.mark.parametrize("change", ["edit", "create", "delete", "rename", "add_model", "add_provider"])
def test_concurrent_profile_changes_survive_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    mgr = _make_manager(tmp_path)
    if change != "create":
        assert mgr.create_sub_agent("worker", "chat-b")[0]
    entered, resume, competing = Event(), Event(), Event()
    write = mgr._write_config
    lock = mgr._config_lock

    class ObservedLock:
        def __enter__(self) -> None:
            if not lock.acquire(blocking=False):
                competing.set()
                lock.acquire()

        def __exit__(self, *args: object) -> None:
            lock.release()

    def slow_first_write(document: dict[str, Any]) -> None:
        if not entered.is_set():
            entered.set()
            assert resume.wait(5)
        write(document)

    def first() -> bool:
        if change == "add_provider":
            return mgr.add_provider("extra", name="Extra")
        if change == "add_model":
            return mgr.add_model("prov", "extra", model="extra")
        if change == "rename":
            return mgr.rename_model("chat-b", "renamed")
        if change == "delete":
            return mgr.remove_sub_agent("worker")[0]
        if change == "create":
            return mgr.create_sub_agent("worker", "chat-b")[0]
        return mgr.update_sub_agent("worker", description="updated")[0]

    def second() -> bool:
        if change in {"add_model", "add_provider"}:
            return mgr.reload_from_disk()["ok"]
        if change == "create":
            return mgr.create_sub_agent("worker", "chat-a")[0]
        return mgr.update_sub_agent("worker", instructions="check")[0]

    monkeypatch.setattr(mgr, "_write_config", slow_first_write)
    monkeypatch.setattr(mgr, "_config_lock", ObservedLock())
    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = pool.submit(first)
        assert entered.wait(5)
        competitor = pool.submit(second)
        try:
            assert competing.wait(5)
        finally:
            resume.set()
        assert initial.result()
        assert competitor.result() == (change in {"edit", "rename", "add_model", "add_provider"})

    loaded = LLMManager(str(tmp_path / "llm.json"))
    assert mgr.list_sub_agents() == loaded.list_sub_agents()
    if change == "add_provider":
        assert loaded.get_provider("extra") is not None and mgr.get_provider("extra") is not None
    if change == "add_model":
        assert loaded.get_client("extra") is not None and mgr.get_client("extra") is not None
    profile = loaded.get_sub_agent_profile("worker")
    if change == "delete":
        assert profile is None
    else:
        assert profile is not None
        assert profile.models == (["renamed"] if change == "rename" else ["chat-b"])
        if change in {"edit", "rename"}:
            assert profile.facets.instructions == "check"
        if change == "edit":
            assert profile.description == "updated"


def test_failed_model_writes_preserve_registry_and_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _make_manager(tmp_path)
    assert mgr.create_sub_agent("worker", "chat-b")[0]
    client = mgr.get_client("chat-b")
    before = (tmp_path / "llm.json").read_bytes()

    def fail(document: dict[str, Any]) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(mgr, "_write_config", fail)
    with pytest.raises(OSError):
        mgr.rename_model("chat-b", "new")
    with pytest.raises(OSError):
        mgr.add_provider("extra")
    with pytest.raises(OSError):
        mgr.add_model("prov", "extra")
    assert mgr.get_provider("extra") is None and mgr.get_client("extra") is None
    assert mgr.get_client("chat-b") is client and mgr.get_client("new") is None
    assert mgr.resolve_sub_agent_model("worker") == "chat-b"
    assert (tmp_path / "llm.json").read_bytes() == before


def test_profile_save_preserves_temporarily_unreadable_config(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    path = tmp_path / "llm.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(OSError):
        mgr.create_sub_agent("worker", "chat-b")
    assert path.read_text("utf-8") == "{"
    assert mgr.get_sub_agent_profile("worker") is None


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


class TestFacets:
    """档案执行面：instructions / tool_tags / blocked_tools / output_schema。"""

    def test_create_with_facets(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        ok, _ = mgr.create_sub_agent(
            "scout", "chat-b", "调研专员",
            instructions="只读调研，禁止修改文件",
            tool_tags="heartbeat, web",
            blocked_tools=["run_shell_command"],
            output_schema='{"summary": "", "facts": []}',
        )
        assert ok
        p = _profile(mgr, "scout")
        assert p["instructions"] == "只读调研，禁止修改文件"
        assert p["tool_tags"] == ["heartbeat", "web"]
        assert p["blocked_tools"] == ["run_shell_command"]
        assert p["output_schema"] == {"summary": "", "facts": []}
        # 持久化往返
        mgr2 = LLMManager(str(tmp_path / "llm.json"))
        p2 = _profile(mgr2, "scout")
        assert p2["instructions"] == "只读调研，禁止修改文件"
        assert p2["output_schema"]["facts"] == []

    def test_create_rejects_invalid_schema(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        ok, message = mgr.create_sub_agent("bad", "chat-a", output_schema="not json")
        assert not ok and "output_schema" in message

    def test_update_clear_semantics(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        mgr.create_sub_agent("x1", "chat-a", instructions="守则",
                             tool_tags=["web"], output_schema={"a": 1})
        ok, _ = mgr.update_sub_agent(
            "x1", instructions="clear", tool_tags=[], output_schema="clear",
        )
        assert ok
        p = _profile(mgr, "x1")
        assert p["instructions"] == "" and p["tool_tags"] == [] and p["output_schema"] is None

    def test_builtin_rejects_facets(self, tmp_path) -> None:
        mgr = _make_manager(tmp_path)
        ok, message = mgr.update_sub_agent("easy", instructions="守则")
        assert not ok and "执行面" in message
        # 模型池更新不受影响
        ok, _ = mgr.update_sub_agent("easy", models=["chat-a"])
        assert ok


def test_profile_writes_can_be_retried_after_disk_failure(tmp_path, monkeypatch) -> None:
    mgr = _make_manager(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(mgr, "save_config", lambda **kwargs: False)
        with pytest.raises(OSError):
            mgr.create_sub_agent("retry", "chat-a")
        assert mgr.get_sub_agent_profile("retry") is None
    assert mgr.create_sub_agent("retry", "chat-a", description="old")[0]
    path = tmp_path / "llm.json"
    before = path.read_bytes()
    assert not mgr.update_sub_agent("retry", model_id="chat-b", description="bad", output_schema=[])[0]
    with monkeypatch.context() as patch:
        patch.setattr(mgr, "save_config", lambda **kwargs: False)
        with pytest.raises(OSError):
            mgr.update_sub_agent("retry", model_id="chat-b", description="new")
        with pytest.raises(OSError):
            mgr.remove_sub_agent("retry")
    profile = _profile(mgr, "retry")
    assert profile["models"] == ["chat-a"] and profile["description"] == "old"
    assert path.read_bytes() == before
    assert mgr.update_sub_agent("retry", model_id="chat-b", description="")[0]
    assert _profile(mgr, "retry")["description"] == ""
    assert mgr.remove_sub_agent("retry")[0]
