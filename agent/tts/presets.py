"""音色预设注册表 — AI 与 Web 共用的音色库（增删改查 + 场景指派）。

预设是命名的音色单元：预置音色 ID（voice_id）或克隆参考对
（reference_audio + reference_text）二选一，附注释。场景指派存配置键
（sound_voice_default / sound_voice_realtime，值为预设 ID），指派与预设
分离——被指派的预设拒绝删除（先解除指派）。存储为用户状态文件
（线程锁 + 原子写，provider_keys 同款纪律）。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Dict, List, Optional

from core.config import ConfigManager
from core.log import log
from core.path import ConfigPaths

_LOG_TAG = "语音合成"

SCENE_KEYS: Dict[str, str] = {
    "default": "sound_voice_default",
    "realtime": "sound_voice_realtime",
}
"""场景 → 指派配置键（default 全局默认 / realtime 通话，空值=未指派）。"""

_STORE_LOCK = Lock()


@dataclass
class VoicePreset:
    """一条音色预设（预置音色或克隆参考对，二选一）。"""

    id: str
    name: str
    voice_id: str = ""
    reference_audio: str = ""
    reference_text: str = ""
    note: str = ""


def _store_path() -> str:
    return str(ConfigPaths.VOICE_PRESETS)


def _load() -> List[VoicePreset]:
    try:
        with open(_store_path(), encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("presets", []) if isinstance(data, dict) else []
        return [
            VoicePreset(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                voice_id=str(item.get("voice_id", "") or ""),
                reference_audio=str(item.get("reference_audio", "") or ""),
                reference_text=str(item.get("reference_text", "") or ""),
                note=str(item.get("note", "") or ""),
            )
            for item in items if isinstance(item, dict) and item.get("id")
        ]
    except FileNotFoundError:
        return []
    except Exception as exc:
        log(f"音色预设库读取失败（按空处理）: {exc}", "WARNING", tag=_LOG_TAG)
        return []


def _persist(presets: List[VoicePreset]) -> None:
    from pathlib import Path

    from core.file_utils import atomic_write_text

    payload = json.dumps(
        {"presets": [asdict(p) for p in presets]}, ensure_ascii=False, indent=2)
    atomic_write_text(Path(_store_path()), payload)


def list_presets() -> List[VoicePreset]:
    with _STORE_LOCK:
        return _load()


def get_preset(preset_id: str) -> Optional[VoicePreset]:
    preset_id = preset_id.strip()
    if not preset_id:
        return None
    for preset in list_presets():
        if preset.id == preset_id:
            return preset
    return None


def save_preset(
    *,
    id: str = "",
    name: str = "",
    voice_id: str = "",
    reference_audio: str = "",
    reference_text: str = "",
    note: str = "",
) -> VoicePreset:
    """新建（id 空）或更新预设；校验失败 raise ValueError。"""

    def _clean(value: str) -> str:
        return str(value or "").strip()

    preset_id, name = _clean(id), _clean(name)
    voice_id, note = _clean(voice_id), _clean(note)
    reference_audio, reference_text = _clean(reference_audio), _clean(reference_text)
    if not name:
        raise ValueError("预设名称不能为空")
    if bool(voice_id) == bool(reference_audio):
        raise ValueError("预置音色 ID 与克隆参考音频二选一")
    if reference_audio and not reference_text:
        raise ValueError("克隆参考音频必须配参考文本")
    with _STORE_LOCK:
        presets = _load()
        if any(p.name == name and p.id != preset_id for p in presets):
            raise ValueError(f"预设名称已存在: {name}")
        if preset_id:
            for i, existing in enumerate(presets):
                if existing.id == preset_id:
                    updated = VoicePreset(
                        id=preset_id, name=name, voice_id=voice_id,
                        reference_audio=reference_audio,
                        reference_text=reference_text, note=note)
                    presets[i] = updated
                    _persist(presets)
                    return updated
            raise ValueError(f"预设不存在: {preset_id}")
        preset = VoicePreset(
            id=f"vp_{secrets.token_hex(4)}", name=name, voice_id=voice_id,
            reference_audio=reference_audio, reference_text=reference_text,
            note=note)
        presets.append(preset)
        _persist(presets)
        return preset


def remove_preset(preset_id: str) -> None:
    """删除预设；被场景指派的预设拒绝删除（先解除指派）。"""
    preset_id = preset_id.strip()
    with _STORE_LOCK:
        presets = _load()
        if not any(p.id == preset_id for p in presets):
            raise ValueError(f"预设不存在: {preset_id}")
        assigned = [
            scene for scene, key in SCENE_KEYS.items()
            if str(ConfigManager.get(key, "") or "").strip() == preset_id]
        if assigned:
            raise ValueError(
                f"预设正被指派为{'/'.join(assigned)}音色，先解除指派再删除")
        _persist([p for p in presets if p.id != preset_id])


def assigned_preset(scene: str) -> Optional[VoicePreset]:
    """场景当前指派的预设（未指派或指派已悬空返回 None）。"""
    key = SCENE_KEYS.get(scene)
    if key is None:
        return None
    return get_preset(str(ConfigManager.get(key, "") or ""))


def assign_voice(scene: str, preset_id: str) -> None:
    """场景指派：preset_id 空=清除指派（realtime 即跟随默认）；非空须存在。"""
    if scene not in SCENE_KEYS:
        raise ValueError(f"未知场景: {scene}（可选 {' / '.join(SCENE_KEYS)}）")
    preset_id = preset_id.strip()
    if preset_id and get_preset(preset_id) is None:
        raise ValueError(f"预设不存在: {preset_id}")
    ConfigManager.set(SCENE_KEYS[scene], preset_id)
    ConfigManager.save()
