"""音色解析 — 全部合成入口的单一音色决策链。

链序：调用点显式音色 → 场景覆盖（realtime_tts_voice，空 = 跟随默认）
→ 全局默认（tts_default_voice）→ 提供者协议音色（各提供者代码内持有，
仅在全局默认未配置时生效）。声音页与 sound_config 工具是音色配置的
全部入口；任何合成路径不得绕开本模块另读音色键。
"""

from __future__ import annotations

from core.config import ConfigManager


def default_voice() -> str:
    """全局默认音色（未配置为空串，由提供者落到协议音色）。"""
    return str(ConfigManager.get("tts_default_voice", "") or "").strip()


def realtime_voice() -> str:
    """实时通话音色：通话覆盖优先，空则跟随全局默认。"""
    override = str(ConfigManager.get("realtime_tts_voice", "") or "").strip()
    return override or default_voice()


def resolve_voice(voice: str, protocol_voice: str) -> str:
    """显式音色 → 全局默认 → 提供者协议音色。"""
    return (voice or "").strip() or default_voice() or protocol_voice
