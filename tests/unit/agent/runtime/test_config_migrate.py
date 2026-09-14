"""旧实体配置一次性迁移（agent.runtime.config_migrate）单元测试。"""

from __future__ import annotations

import json
import os

import pytest

from agent.runtime import config_migrate


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


def _write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestMediaConfigMigration:
    def test_full_media_config_imported(self, tmp_path, mem_config):
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

        assert mem_config["tts_default_voice"] == "female-yujie"
        assert mem_config["tts_default_reference_audio"] == "workspace/ref.mp3"
        assert mem_config["tts_default_reference_text"] == "参考文本"
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

    def test_existing_new_values_not_overwritten(self, tmp_path, mem_config):
        mem_config["tts_default_voice"] = "already-set"
        _write(str(tmp_path / "media" / "config.json"), {"default_voice": "legacy"})

        config_migrate.migrate_legacy_entity_configs(str(tmp_path))

        assert mem_config["tts_default_voice"] == "already-set"

    def test_idempotent_second_run(self, tmp_path, mem_config):
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
