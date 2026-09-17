"""音色解析 — 全部合成入口的单一音色决策链。

场景指派（音色预设 ID）→ 预设内容 → 提供者协议音色：默认场景取默认
预设（克隆对供一次性合成，voice_id 供流式管线），通话场景未指派时跟随
默认预设的 voice_id；无任何指派时落到提供者协议音色（各提供者代码内
持有，仅此层生效）。预设库与场景指派由 voice_preset 工具（AI）与
声音页·音色面板（Web）共用管理；任何合成路径不得绕开本模块另读音色
配置。
"""

from __future__ import annotations

from typing import Optional

from agent.tts.presets import VoicePreset, assigned_preset
from core.config import ConfigManager


def default_preset() -> Optional[VoicePreset]:
    """默认场景预设（未指派为 None）。"""
    return assigned_preset("default")


def realtime_preset() -> Optional[VoicePreset]:
    """通话场景预设（未指派跟随默认预设）。"""
    return assigned_preset("realtime") or default_preset()


def default_voice() -> str:
    """全局默认音色 ID（默认预设为克隆型或未指派时为空串）。"""
    preset = default_preset()
    return preset.voice_id if preset else ""


def realtime_voice() -> str:
    """通话音色 ID（克隆型预设不适用流式通话，为空时落协议音色）。"""
    preset = realtime_preset()
    return preset.voice_id if preset else ""


def resolve_voice(voice: str, protocol_voice: str) -> str:
    """显式音色 → 默认预设音色 → 提供者协议音色。"""
    return (voice or "").strip() or default_voice() or protocol_voice


def scene_assignment(scene: str) -> str:
    """场景当前指派的预设 ID（空=未指派；诊断展示用）。"""
    from agent.tts.presets import SCENE_KEYS

    key = SCENE_KEYS.get(scene)
    if key is None:
        return ""
    return str(ConfigManager.get(key, "") or "").strip()
