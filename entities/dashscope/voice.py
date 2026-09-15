"""百炼声音能力组件 — 一次性合成 + 音色管理（复刻/列表/删除）。

tts：SpeechSynthesizer.call 整段合成（mp3 字节），走声音能力路由的
text_to_voice 工具；
voice_mgmt：VoiceEnrollmentService 音色复刻（create/query/list/delete）。
复刻 API 要求可公网访问的音频 URL——源是 URL 直用；本地文件经
dashscope_clone_upload_url 配置的上传端点换取直链（未配置时给出
明确指引而非静默失败）。
"""

from __future__ import annotations

import re
from typing import Any, Dict

from core.config import ConfigManager
from core.log import log
from entities._sdk import CapabilityNotSupported, ProviderUnavailable

from . import sdk

_LOG_TAG = "百炼"

_BUILTIN_VOICES = [
    {"voice_id": "longanhuan_v3.6", "desc": "龙安欢（女，通用）"},
    {"voice_id": "longxiaochun_v2", "desc": "龙小淳（女，温柔）"},
    {"voice_id": "longxiaoxia_v2", "desc": "龙小夏（女，活泼）"},
    {"voice_id": "longxiaocheng_v2", "desc": "龙小诚（男，通用）"},
    {"voice_id": "longxiaobai_v2", "desc": "龙小白（女，亲切）"},
]


class DashScopeSoundProvider:
    """阿里百炼声音能力组件：一次性语音合成 + 音色管理。"""

    name = "dashscope"
    capabilities = frozenset({"tts", "voice_mgmt"})

    def is_configured(self, capability: str) -> bool:
        return sdk.sdk_ready()

    def status_details(self, capability: str) -> Dict[str, Any]:
        return {"model": str(ConfigManager.get("dashscope_tts_model", "")),
                "unavailable_hint": sdk.unavailable_hint()}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        if capability == "tts":
            return await self._tts(**kwargs)
        if capability == "voice_mgmt":
            return await self._voice_mgmt(**kwargs)
        raise CapabilityNotSupported(f"dashscope 组件不支持能力 '{capability}'")

    # ------------------------------------------------------------------
    # 一次性合成
    # ------------------------------------------------------------------

    async def _tts(
        self, *, text: str, voice: str, references=None, emotion: str = "",
        speed: float = 0.0, pitch: int = 0, language_boost: str = "",
    ) -> Dict[str, Any]:
        if references:
            raise CapabilityNotSupported("百炼合成不支持参考音频（复刻音色经 clone_voice 注册后按 voice 引用）")
        ds, reason = sdk.import_sdk()
        if ds is None:
            raise ProviderUnavailable(reason)
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        model = str(ConfigManager.get("dashscope_tts_model", "qwen-audio-3.0-tts-plus"))
        if not voice:
            voice = str(ConfigManager.get("dashscope_tts_voice", "longanhuan_v3.6"))
        synthesizer = SpeechSynthesizer(
            model=model, voice=voice,
            speech_rate=float(speed) if speed else 1.0)
        audio = await sdk.run_sync(synthesizer.call, text)
        if not audio:
            raise RuntimeError("百炼合成未返回音频")
        return {"audio_bytes": audio, "model": model}

    # ------------------------------------------------------------------
    # 音色管理
    # ------------------------------------------------------------------

    async def _voice_mgmt(self, *, op: str, **kwargs: Any) -> Dict[str, Any]:
        ds, reason = sdk.import_sdk()
        if ds is None:
            raise ProviderUnavailable(reason)
        from dashscope.audio.tts_v2.enrollment import VoiceEnrollmentService

        service = VoiceEnrollmentService()

        if op == "list":
            voices = await sdk.run_sync(service.list_voices, None, 0, 100)
            cloned = [
                {"voice_id": v.get("voice_id", ""), "status": v.get("status", ""),
                 "created_at": v.get("gmt_create", "")}
                for v in (voices or [])
            ]
            return {
                "success": True,
                "provider": self.name,
                "system_voices": _BUILTIN_VOICES,
                "voice_cloning": cloned,
                "note": "系统音色仅列常用项，完整列表见百炼控制台",
            }

        if op == "clone":
            url = await self._clone_source_url(kwargs)
            model = str(ConfigManager.get("dashscope_tts_model", "qwen-audio-3.0-tts-plus"))
            prefix = self._clone_prefix(kwargs.get("voice_id", ""))
            voice_id = await sdk.run_sync(
                service.create_voice, model, prefix, url)
            out: Dict[str, Any] = {
                "success": True, "provider": self.name,
                "voice_id": voice_id, "source_url": url,
            }
            preview = str(kwargs.get("preview_text", "") or "").strip()
            if preview:
                try:
                    trial = await self._tts(
                        text=preview[:500], voice=voice_id, references=None)
                    out["trial_audio_bytes"] = trial["audio_bytes"]
                except Exception as exc:
                    log(f"百炼复刻试听失败（音色已注册）: {exc}", "DEBUG", tag=_LOG_TAG)
            out.setdefault("hint", f"复刻音色 '{voice_id}' 已注册，text_to_voice 的 voice 参数可直接使用")
            return out

        if op == "delete":
            voice_id = str(kwargs.get("voice_id", "") or "").strip()
            if not voice_id:
                raise ProviderUnavailable("未提供 voice_id")
            await sdk.run_sync(service.delete_voice, voice_id)
            return {"success": True, "provider": self.name, "deleted": voice_id}

        raise CapabilityNotSupported(f"百炼组件不支持音色操作 '{op}'（design 声音设计未接入）")

    async def _clone_source_url(self, kwargs: Dict[str, Any]) -> str:
        """复刻源 URL：直传 URL 直用；本地文件经配置的上传端点换直链。"""
        source_url = str(kwargs.get("source_url", "") or "").strip()
        if source_url:
            return source_url
        upload_url = str(ConfigManager.get("dashscope_clone_upload_url", "") or "").strip()
        resolved = str(kwargs.get("resolved", "") or "")
        if not upload_url:
            raise ProviderUnavailable(
                "百炼复刻需要可公网访问的音频 URL：传入 http(s) 源直用；本地文件"
                "需在配置 dashscope_clone_upload_url 配置上传端点（或先把音频"
                "传到可直链的对象存储）")
        import os

        import httpx

        mime = "audio/wav" if resolved.endswith(".wav") else "audio/mpeg"
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            with open(resolved, "rb") as fh:
                resp = await client.post(
                    upload_url, files={"file": (os.path.basename(resolved), fh, mime)})
        resp.raise_for_status()
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        for key in ("downloadLink", "download_link", "url", "direct_link", "link", "download_url"):
            if key in data:
                return str(data[key])
        raise ProviderUnavailable(f"上传端点未返回可用的直链字段: {list(data)[:8]}")

    @staticmethod
    def _clone_prefix(voice_id: str) -> str:
        """DashScope 复刻前缀：小写字母/数字 ≤10 字符。"""
        prefix = re.sub(r"[^a-z0-9]", "", str(voice_id or "").lower())[:10]
        return prefix or "anelf"
