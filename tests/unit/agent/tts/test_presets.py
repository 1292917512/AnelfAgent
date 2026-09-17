"""音色预设注册表测试：增删改查 / 校验 / 场景指派与删除保护。"""

from __future__ import annotations

import json

import pytest

from agent.tts import presets
from core.config import ConfigManager


@pytest.fixture
def preset_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """预设存储与指派配置键全部隔离到临时域。"""
    target = tmp_path / "voice_presets.json"
    monkeypatch.setattr(presets, "_store_path", lambda: str(target))
    store: dict = {}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(ConfigManager, "set", staticmethod(lambda k, v: store.__setitem__(k, v)))
    monkeypatch.setattr(ConfigManager, "save", staticmethod(lambda: True))
    return target


class TestCrud:
    def test_create_persists_and_lists(self, preset_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie", note="沉稳")
        assert preset.id.startswith("vp_")
        data = json.loads(preset_env.read_text(encoding="utf-8"))
        assert data["presets"][0]["name"] == "御姐"
        assert presets.list_presets()[0].voice_id == "female-yujie"

    def test_update_keeps_id(self, preset_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        updated = presets.save_preset(id=preset.id, name="少女", voice_id="female-qingse")
        assert updated.id == preset.id and updated.name == "少女"
        assert len(presets.list_presets()) == 1

    def test_update_unknown_id_rejected(self, preset_env) -> None:
        with pytest.raises(ValueError, match="不存在"):
            presets.save_preset(id="vp_dead", name="x", voice_id="v")

    def test_clone_preset_roundtrip(self, preset_env) -> None:
        preset = presets.save_preset(
            name="克隆音", reference_audio="workspace/uploads/a.mp3", reference_text="你好")
        loaded = presets.get_preset(preset.id)
        assert loaded is not None and loaded.reference_audio.endswith("a.mp3")


class TestValidation:
    def test_name_required(self, preset_env) -> None:
        with pytest.raises(ValueError, match="名称"):
            presets.save_preset(voice_id="v")

    def test_duplicate_name_rejected(self, preset_env) -> None:
        presets.save_preset(name="御姐", voice_id="female-yujie")
        with pytest.raises(ValueError, match="名称已存在"):
            presets.save_preset(name="御姐", voice_id="another")
        # 自己改名到原名的更新不受影响
        preset = presets.list_presets()[0]
        presets.save_preset(id=preset.id, name="御姐", voice_id="female-yujie")

    def test_voice_and_reference_mutually_exclusive(self, preset_env) -> None:
        with pytest.raises(ValueError, match="二选一"):
            presets.save_preset(
                name="x", voice_id="v",
                reference_audio="http://a/b.mp3", reference_text="t")

    def test_reference_requires_text(self, preset_env) -> None:
        with pytest.raises(ValueError, match="参考文本"):
            presets.save_preset(name="x", reference_audio="http://a/b.mp3")

    def test_delete_unknown_rejected(self, preset_env) -> None:
        with pytest.raises(ValueError, match="不存在"):
            presets.remove_preset("vp_dead")


class TestAssignment:
    def test_assign_and_resolve(self, preset_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        presets.assign_voice("default", preset.id)
        assert presets.assigned_preset("default") == preset
        assert presets.assigned_preset("realtime") is None  # 未指派

    def test_realtime_follows_default(self, preset_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        presets.assign_voice("default", preset.id)
        from agent.tts.voice import realtime_preset
        assert realtime_preset() == preset

    def test_clear_assignment(self, preset_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        presets.assign_voice("default", preset.id)
        presets.assign_voice("default", "")
        assert presets.assigned_preset("default") is None

    def test_assign_unknown_scene_or_preset(self, preset_env) -> None:
        with pytest.raises(ValueError, match="未知场景"):
            presets.assign_voice("night", "x")
        with pytest.raises(ValueError, match="不存在"):
            presets.assign_voice("default", "vp_dead")

    def test_assigned_preset_refuses_delete(self, preset_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        presets.assign_voice("realtime", preset.id)
        with pytest.raises(ValueError, match="先解除指派"):
            presets.remove_preset(preset.id)
        presets.assign_voice("realtime", "")
        presets.remove_preset(preset.id)
        assert presets.list_presets() == []

    def test_dangling_assignment_resolves_none(self, preset_env) -> None:
        # 指派指向已消失的预设（预设文件被外部编辑）不炸，按未指派处理
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        presets.assign_voice("default", preset.id)
        preset_env.write_text(json.dumps({"presets": []}), encoding="utf-8")
        assert presets.assigned_preset("default") is None
