"""LLMManager.reload_from_disk 热重载 reconcile 单元测试。

覆盖：手改 llm_clients.json 后的精准 reconcile 语义——不变模型对象身份保留
（运行时学习状态不丢）、变化原地更新、增删修正默认、损坏文件保现状、
${ENV_VAR} 引用重载稳定、supports_video/extra_headers 回读。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.llm.llm_manager import LLMManager


def _write(path: Path, data: dict) -> None:  # type: ignore[type-arg]
    path.write_text(json.dumps(data), encoding="utf-8")


def _base_config() -> dict:  # type: ignore[type-arg]
    return {
        "default_chat": "m1",
        "providers": [{
            "id": "p1", "api_type": "openai", "base_url": "http://a", "api_key": "k1",
            "models": [{"id": "m1", "model": "m1-x"}],
        }],
        "type_priorities": {"chat": ["m1"]},
    }


@pytest.fixture()
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "llm_clients.json"
    _write(cfg, _base_config())
    return LLMManager(str(cfg)), cfg


class TestReloadReconcile:
    def test_manual_add_provider_and_model(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        data = _base_config()
        data["providers"].append({
            "id": "p2", "api_type": "openai", "base_url": "http://b", "api_key": "k2",
            "models": [{"id": "m2", "model": "m2-x"}],
        })
        _write(cfg, data)

        summary = manager.reload_from_disk()
        assert summary["ok"] is True
        assert summary["providers"]["added"] == ["p2"]
        assert summary["models"]["added"] == ["m2"]
        client = manager.get_client("m2")
        assert client is not None and client.config.base_url == "http://b"
        # 新增模型自动补全进类型优先级列表
        assert "m2" in [item["id"] for item in manager.get_type_priorities()["chat"]]

    def test_unchanged_preserves_client_identity(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, _cfg = env
        client = manager.get_client("m1")
        assert client is not None
        # 运行时学习状态：reload 无变化时不得丢失
        client._learned_dropped_params.add("some_param")
        client._responses_native_blocked = True

        summary = manager.reload_from_disk()
        assert summary["ok"] is True
        assert not summary["models"]["changed"] and not summary["models"]["added"]
        assert manager.get_client("m1") is client
        assert client._learned_dropped_params == {"some_param"}
        assert client._responses_native_blocked is True

    def test_changed_model_updated_in_place(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        client = manager.get_client("m1")
        assert client is not None
        client._learned_output_cap = 4096

        data = _base_config()
        data["providers"][0]["models"][0]["timeout"] = 99
        _write(cfg, data)
        summary = manager.reload_from_disk()

        assert summary["models"]["changed"] == ["m1"]
        assert manager.get_client("m1") is client  # 对象身份保留（Mind.llm 等引用不失效）
        assert client.config.timeout == 99
        # update_config 语义：配置变化后旧的输出上限学习值不应继续钳制
        assert client._learned_output_cap is None

    def test_removed_model_cleans_priorities(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        data = _base_config()
        data["providers"].append({
            "id": "p2", "base_url": "http://b",
            "models": [{"id": "m2", "model": "m2-x"}],
        })
        data["type_priorities"]["chat"] = ["m1", "m2"]
        data["default_chat"] = "m2"
        _write(cfg, data)
        manager.reload_from_disk()

        # 从文件中删除 m1
        data["providers"][0]["models"] = []
        _write(cfg, data)
        summary = manager.reload_from_disk()
        assert summary["models"]["removed"] == ["m1"]
        assert manager.get_client("m1") is None
        chat_list = [item["id"] for item in manager.get_type_priorities()["chat"]]
        assert "m1" not in chat_list and "m2" in chat_list

    def test_removed_provider_cascades(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        _write(cfg, {"default_chat": "", "providers": [], "type_priorities": {}})
        summary = manager.reload_from_disk()
        assert summary["providers"]["removed"] == ["p1"]
        assert summary["models"]["removed"] == ["m1"]
        assert manager.get_provider("p1") is None
        assert manager.get_client("m1") is None

    def test_default_chat_change_applied(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        data = _base_config()
        data["providers"][0]["models"].append({"id": "m2", "model": "m2-x"})
        data["default_chat"] = "m2"
        _write(cfg, data)
        summary = manager.reload_from_disk()
        assert summary["default_chat"] == "m2"
        assert manager.default_name == "m2"

    def test_corrupt_json_preserves_state(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        cfg.write_text("{broken json", encoding="utf-8")
        summary = manager.reload_from_disk()
        assert summary["ok"] is False
        assert "error" in summary
        assert manager.get_client("m1") is not None
        # 损坏文件不被改名备份（用户可能正在编辑中）
        assert cfg.exists()

    def test_missing_file_error(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        cfg.unlink()
        summary = manager.reload_from_disk()
        assert summary["ok"] is False
        assert manager.get_client("m1") is not None

    def test_provider_connection_change_propagates(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        client = manager.get_client("m1")
        assert client is not None
        data = _base_config()
        data["providers"][0]["base_url"] = "http://b"
        data["providers"][0]["api_key"] = "k2"
        _write(cfg, data)
        summary = manager.reload_from_disk()
        assert summary["providers"]["changed"] == ["p1"]
        assert summary["models"]["changed"] == ["m1"]
        assert manager.get_client("m1") is client
        assert client.config.base_url == "http://b" and client.config.api_key == "k2"

    def test_env_ref_reload_stable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RELOAD_TEST_KEY", "sk-real-789")
        cfg = tmp_path / "llm_clients.json"
        data = _base_config()
        data["providers"][0]["api_key"] = "${RELOAD_TEST_KEY}"
        _write(cfg, data)
        manager = LLMManager(str(cfg))
        client = manager.get_client("m1")
        assert client is not None and client.config.api_key == "sk-real-789"
        # 磁盘是引用、内存是展开值：reload 不应产生伪 diff
        summary = manager.reload_from_disk()
        assert summary["ok"] is True
        assert not summary["providers"]["changed"] and not summary["models"]["changed"]
        assert manager.get_client("m1") is client

    def test_supports_video_and_extra_headers_roundtrip(self, tmp_path: Path) -> None:
        cfg = tmp_path / "llm_clients.json"
        data = _base_config()
        data["providers"][0]["models"][0]["supports_video"] = True
        data["providers"][0]["models"][0]["extra_headers"] = {"X-Trace": "1"}
        _write(cfg, data)
        manager = LLMManager(str(cfg))
        client = manager.get_client("m1")
        assert client is not None
        assert client.config.supports_video is True
        assert client.config.extra_headers == {"X-Trace": "1"}
        # 这些字段已回读：reload 不再误报 diff（此前写盘但不回读会导致 reconcile 误重置）
        summary = manager.reload_from_disk()
        assert not summary["models"]["changed"]
        assert client.config.supports_video is True

    def test_sub_agents_applied(self, env) -> None:  # type: ignore[no-untyped-def]
        manager, cfg = env
        data = _base_config()
        data["sub_agents"] = {
            "easy": {"models": ["m1"]},
            "researcher": {"models": ["m1"], "description": "调研"},
        }
        _write(cfg, data)
        summary = manager.reload_from_disk()
        assert summary["ok"] is True
        profile = manager.get_sub_agent_profile("researcher")
        assert profile is not None and profile.models == ["m1"]
        easy = manager.get_sub_agent_profile("easy")
        assert easy is not None and easy.models == ["m1"]
