"""播放器域 — 媒体播放器的播放控制、音量调节与语音播报。"""

from __future__ import annotations

from typing import Dict

from core.tool_errors import ErrorCause

from ..framework import DeviceDomain, device_domain
from ..models import ActionSpec, DeviceState, ServiceCall, SmartHomeCallError

_PLAYER_STATES = {
    "playing": "播放中",
    "paused": "已暂停",
    "idle": "待机",
    "off": "关",
    "on": "开",
}


def _volume(raw: str) -> float:
    """解析音量百分比并转为 0-1 电平。"""
    value = int(float(raw))
    if not 0 <= value <= 100:
        raise ValueError("超出 0-100 范围")
    return value / 100


@device_domain
class MediaPlayerDomain(DeviceDomain):
    """媒体播放器（播放/暂停/音量/语音播报/播放音频 URL）。

    语音播报经 HA TTS 服务（tts 域）跨域调用，音箱接入方式不限
    （DLNA / 米家 miot 等任何提供 media_player 实体的供应商）。
    """

    key = "media_player"
    display_name = "播放器"
    description = "音箱、电视等媒体播放器（播放/暂停/音量/语音播报；语音播报需配置 tts_service）"
    priority = 30
    ha_domains = ("media_player",)
    config_schema = {
        **DeviceDomain.config_schema,
        "tts_service": {
            "description": "语音播报的 TTS 服务（HA 服务全名，如 tts.cloud_say / tts.google_translate_say / tts.edge_tts_say；留空则 speak 动作不可用）",
            "default": "",
        },
    }

    def format_state(self, device: DeviceState) -> str:
        text = _PLAYER_STATES.get(device.state, device.state)
        if device.state == "playing":
            title = str(device.attributes.get("media_title") or "").strip()
            if title:
                text = f"{text}《{title}》"
        volume = device.attributes.get("volume_level")
        if device.state != "off" and isinstance(volume, (int, float)):
            text = f"{text} · 音量 {round(volume * 100)}%"
        return text

    def actions(self) -> Dict[str, ActionSpec]:
        return {
            "turn_on": ActionSpec("turn_on", "打开"),
            "turn_off": ActionSpec("turn_off", "关闭"),
            "media_play": ActionSpec("media_play", "播放"),
            "media_pause": ActionSpec("media_pause", "暂停"),
            "set_volume": ActionSpec(
                "volume_set", "设置音量", value_param="volume_level",
                value_hint="音量百分比 0-100", convert=_volume,
            ),
            # service 在 build_service_call 按 tts_service 配置动态解析，此处仅作声明
            "speak": ActionSpec(
                "", "语音播报", value_param="message",
                value_hint="播报文本（需配置 tts_service）",
            ),
            "play_media": ActionSpec(
                "play_media", "播放音频", value_param="media_content_id",
                value_hint="音频 URL",
            ),
        }

    def build_service_call(self, action: str, raw_value: str = "") -> ServiceCall:
        """解析服务调用：speak 跨域走配置的 TTS 服务，play_media 补内容类型。"""
        name = action.strip()
        if name == "speak":
            return self._build_speak_call(raw_value)
        call = super().build_service_call(action, raw_value)
        if name == "play_media":
            call = ServiceCall(
                call.ha_domain, call.service,
                {**call.data, "media_content_type": "music"},
            )
        return call

    def _build_speak_call(self, raw_value: str) -> ServiceCall:
        """语音播报调用：tts 域 _say 服务，entity_id 为目标音箱。"""
        text = raw_value.strip()
        if not text:
            raise SmartHomeCallError(
                "动作 speak 需要 value 参数（播报文本）", ErrorCause.PARAM,
            )
        tts_service = str(self.get_config("tts_service", "")).strip()
        if not tts_service or "." not in tts_service:
            raise SmartHomeCallError(
                "语音播报未配置 TTS 服务（请设置 "
                "smart_home_media_player_tts_service，"
                "如 tts.cloud_say / tts.google_translate_say）",
                ErrorCause.CONFIG,
            )
        ha_domain, service = tts_service.split(".", 1)
        return ServiceCall(ha_domain, service, {"message": text})
