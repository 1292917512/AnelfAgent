"""旧实体配置一次性迁移（agent.runtime.config_migrate）单元测试。"""

from __future__ import annotations

import json
import os

import pytest

from agent.runtime import config_migrate


@pytest.fixture(autouse=True)
def _isolate_provider_keys(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """凭据迁移隔离：三个源/目标路径全部重定向到临时区（防动真文件）。"""
    from core import provider_keys as pk

    monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "keys.json"))
    monkeypatch.setattr(pk, "_cache", None)
    monkeypatch.setattr(config_migrate, "_minimax_config_path",
                        lambda: str(tmp_path / "entities" / "minimax" / "config.json"))
    monkeypatch.setattr(config_migrate, "_llm_clients_path",
                        lambda: str(tmp_path / "llm_clients.json"))


@pytest.fixture
def mem_config(monkeypatch: pytest.MonkeyPatch):
    """内存态配置隔离。"""
    from core.config import ConfigManager
    store: dict = {}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(ConfigManager, "set", staticmethod(lambda k, v: store.__setitem__(k, v)))
    monkeypatch.setattr(ConfigManager, "has", staticmethod(lambda k: k in store))
    monkeypatch.setattr(ConfigManager, "save", staticmethod(lambda: True))
    return store


@pytest.fixture
def preset_store(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """音色预设库隔离到临时域（迁移建档写此文件）。"""
    from agent.tts import presets as tts_presets

    target = tmp_path / "voice_presets.json"
    monkeypatch.setattr(tts_presets, "_store_path", lambda: str(target))
    return target


def _write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestMediaConfigMigration:
    def test_full_media_config_imported(self, tmp_path, mem_config, preset_store):
        _write(str(tmp_path / "media" / "config.json"), {
            "provider_priority": {
                "vision": ["models", "minimax"],
                "tts": ["minimax", "models"],
                "voice_mgmt": ["models"],
                "image_gen": ["models", "minimax"],
                "asr": ["models"],
            },
            "default_voice": "female-yujie",
            "default_reference_audio": "workspace/ref.mp3",
            "default_reference_text": "参考文本",
            "defaults": {"image_size": "1664x928", "video_resolution": "768P", "video_duration": 6},
            "style_presets": {"nekomimi_maid": "猫耳女仆画风"},
        })

        config_migrate.migrate_legacy_entity_configs(str(tmp_path))

        # 默认音色建档为预设（参考对优先，克隆型）并指派默认场景
        from agent.tts import presets as tts_presets
        presets_list = tts_presets.list_presets()
        assert len(presets_list) == 1
        assert presets_list[0].reference_audio == "workspace/ref.mp3"
        assert presets_list[0].reference_text == "参考文本"
        assert mem_config["sound_voice_default"] == presets_list[0].id
        assert mem_config["vision_default_image_size"] == "1664x928"
        assert mem_config["vision_default_video_resolution"] == "768P"
        assert mem_config["vision_default_video_duration"] == 6
        assert mem_config["vision_style_presets"] == {"nekomimi_maid": "猫耳女仆画风"}
        # vision → understand 改名；tts/voice_mgmt 入声音链；asr 不迁移（内部模型链）
        assert mem_config["vision_provider_priority"] == {
            "understand": ["models", "minimax"],
            "image_gen": ["models", "minimax"],
        }
        assert mem_config["sound_provider_priority"] == {
            "tts": ["minimax", "models"],
            "voice_mgmt": ["models"],
        }
        # 源文件归档（幂等标记）
        assert not os.path.exists(str(tmp_path / "media" / "config.json"))
        assert os.path.exists(str(tmp_path / "media" / "config.json.migrated"))

    def test_existing_assignment_not_overwritten(self, tmp_path, mem_config, preset_store):
        mem_config["sound_voice_default"] = "vp_existing"
        _write(str(tmp_path / "media" / "config.json"), {"default_voice": "legacy"})

        config_migrate.migrate_legacy_entity_configs(str(tmp_path))

        assert mem_config["sound_voice_default"] == "vp_existing"
        from agent.tts import presets as tts_presets
        assert tts_presets.list_presets() == []

    def test_legacy_flat_keys_migrate_once(self, tmp_path, mem_config, preset_store):
        mem_config["tts_default_voice"] = "female-yujie"
        mem_config["realtime_tts_voice"] = "qiaopi_mengmei"

        config_migrate.migrate_legacy_entity_configs(str(tmp_path))

        from agent.tts import presets as tts_presets
        presets_list = tts_presets.list_presets()
        assert len(presets_list) == 2
        assert mem_config["sound_voice_default"] == presets_list[0].id
        assert mem_config["sound_voice_realtime"] == presets_list[1].id
        # 预设库非空后再次迁移不重复建档
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert tts_presets.list_presets() == presets_list

    def test_idempotent_second_run(self, tmp_path, mem_config, preset_store):
        _write(str(tmp_path / "media" / "config.json"), {"default_voice": "v1"})
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        snapshot = dict(mem_config)
        # 第二次运行：文件已归档，无变化
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert mem_config == snapshot


class TestWebConfigMigration:
    def test_full_web_config_imported(self, tmp_path, mem_config):
        _write(str(tmp_path / "web" / "config.json"), {
            "proxy": "http://127.0.0.1:7890",
            "active": {"search": "bigmodel", "reader": "auto"},
            "disabled": ["minimax"],
            "provider_keys": {"bigmodel": "sk-secret"},
        })

        config_migrate.migrate_legacy_entity_configs(str(tmp_path))

        assert mem_config["retrieval_proxy"] == "http://127.0.0.1:7890"
        # auto 项不迁移（缺省语义）
        assert mem_config["retrieval_active"] == {"search": "bigmodel"}
        assert mem_config["retrieval_disabled_providers"] == ["minimax"]
        assert mem_config["retrieval_bigmodel_api_key"] == "sk-secret"
        assert os.path.exists(str(tmp_path / "web" / "config.json.migrated"))

    def test_missing_files_are_noop(self, tmp_path, mem_config):
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert mem_config == {}


class TestSsrfKeyMigration:
    def test_legacy_ssrf_key_migrated(self, tmp_path, mem_config):
        mem_config["web_ssrf_protection"] = False
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert mem_config["retrieval_ssrf_protection"] is False

    def test_existing_ssrf_key_not_overwritten(self, tmp_path, mem_config):
        mem_config["web_ssrf_protection"] = False
        mem_config["retrieval_ssrf_protection"] = True
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert mem_config["retrieval_ssrf_protection"] is True


class TestFunasrKeyMigration:
    def test_legacy_endpoint_migrated(self, tmp_path, mem_config):
        mem_config["audiosync_funasr_endpoint"] = "http://nas:10095"
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert mem_config["funasr_endpoint"] == "http://nas:10095"

    def test_empty_legacy_not_migrated(self, tmp_path, mem_config):
        mem_config["audiosync_funasr_endpoint"] = ""
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert "funasr_endpoint" not in mem_config

    def test_existing_new_key_wins(self, tmp_path, mem_config):
        mem_config["audiosync_funasr_endpoint"] = "http://old:1"
        mem_config["funasr_endpoint"] = "http://new:2"
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert mem_config["funasr_endpoint"] == "http://new:2"

    def test_legacy_timeout_migrated(self, tmp_path, mem_config):
        mem_config["audiosync_funasr_timeout"] = "60"
        config_migrate.migrate_legacy_entity_configs(str(tmp_path))
        assert mem_config["funasr_timeout"] == "60"


class TestProviderKeysMigration:
    def test_minimax_entity_keys_migrated(self, tmp_path, mem_config, monkeypatch):
        import json as _json

        from core import provider_keys as pk

        store_file = tmp_path / "keys.json"
        monkeypatch.setattr(pk, "_path", lambda: str(store_file))
        monkeypatch.setattr(pk, "_cache", None)
        monkeypatch.setattr(pk, "_registry", {})

        entity_cfg = tmp_path / "entities" / "minimax" / "config.json"
        entity_cfg.parent.mkdir(parents=True)
        entity_cfg.write_text(_json.dumps({
            "api_key": "sk-mm", "coding_plan_api_key": "sk-cp",
            "coding_plan_api_host": "https://cp.example", "default_voice_id": "v1",
        }), encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            config_migrate._migrate_provider_keys()
        finally:
            os.chdir(cwd)
        assert pk.get_provider_key("minimax") == "sk-mm"
        assert pk.get_provider_key("minimax_coding_plan") == "sk-cp"
        assert pk.get_provider_key("minimax_coding_plan", field="api_host") == "https://cp.example"
        # 源文件：凭据字段清除，参数保留
        after = _json.loads(entity_cfg.read_text(encoding="utf-8"))
        assert "api_key" not in after and after["default_voice_id"] == "v1"

    def test_existing_key_not_overwritten(self, tmp_path, mem_config, monkeypatch):
        import json as _json

        from core import provider_keys as pk

        store_file = tmp_path / "keys.json"
        monkeypatch.setattr(pk, "_path", lambda: str(store_file))
        monkeypatch.setattr(pk, "_cache", None)
        monkeypatch.setattr(pk, "_registry", {})
        pk.set_provider_key("minimax", "api_key", "sk-existing")

        entity_cfg = tmp_path / "entities" / "minimax" / "config.json"
        entity_cfg.parent.mkdir(parents=True)
        entity_cfg.write_text(_json.dumps({"api_key": "sk-old"}), encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            config_migrate._migrate_provider_keys()
        finally:
            os.chdir(cwd)
        assert pk.get_provider_key("minimax") == "sk-existing"

    def test_dashscope_llm_clients_extracted(self, tmp_path, mem_config, monkeypatch):
        import json as _json

        from core import provider_keys as pk

        store_file = tmp_path / "keys.json"
        monkeypatch.setattr(pk, "_path", lambda: str(store_file))
        monkeypatch.setattr(pk, "_cache", None)
        monkeypatch.setattr(pk, "_registry", {})
        (tmp_path / "llm_clients.json").write_text(_json.dumps({
            "providers": [
                {"id": "mm", "base_url": "https://api.minimaxi.com/v1", "api_key": "sk-x"},
                {"id": "ds", "base_url": "https://dashscope.aliyuncs.com/api/v1",
                 "api_key": "sk-ds-from-llm"},
            ]}), encoding="utf-8")
        monkeypatch.setattr(config_migrate, "_llm_clients_path",
                            lambda: str(tmp_path / "llm_clients.json"))
        config_migrate._migrate_provider_keys()
        assert pk.get_provider_key("dashscope") == "sk-ds-from-llm"
