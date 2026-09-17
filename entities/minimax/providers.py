"""MiniMax 能力组件 — 视觉理解/图像生成、语音合成/音色管理、联网检索、流式 TTS。

四类组件分别接入核心路由：
- MiniMaxVisualProvider：understand（Coding Plan 图片理解）+ image_gen → 视觉能力路由
- MiniMaxSoundProvider：tts（一次性合成）+ voice_mgmt（音色复刻/设计/查询/删除）→ 声音能力路由
- MiniMaxSearchProvider：Coding Plan 联网检索 → 检索提供者矩阵
- MiniMaxTtsProvider / MiniMaxWsTtsProvider：HTTP/WS 双传输流式 TTS → 核心 TTS 注册表

凭据经组件凭据中心（provider_keys.json）：minimax（平台按量，tts/音色/图片）与
coding_plan_api_key（Token Plan 订阅，图片理解/检索）独立解析。
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from entities._sdk import (
    SOURCE_CONFIG,
    SOURCE_ENV,
    CapabilityNotSupported,
    ProviderUnavailable,
    RetrievalProvider,
    TtsStream,
    run_coro_sync,
)

# size 像素格式 → MiniMax aspect_ratio 比例的映射表（取最近比例）
_SIZE_TO_RATIO = {
    "1024x1024": "1:1",
    "1664x928": "16:9",
    "928x1664": "9:16",
    "1472x1104": "4:3",
    "1104x1472": "3:4",
    "1376x768": "16:9",
    "768x1376": "9:16",
}

_SEARCH_TIMEOUT = 15.0


def _client() -> Any:
    from entities.minimax.client import MiniMaxClient
    return MiniMaxClient()


def _to_aspect_ratio(image_size: str) -> str:
    """统一 size 参数转 MiniMax 比例：比例格式直通，像素格式映射最近比例。"""
    size = image_size.strip()
    if ":" in size:
        return size
    return _SIZE_TO_RATIO.get(size, "1:1")


# ==================================================================
# 视觉能力组件
# ==================================================================

class MiniMaxVisualProvider:
    """MiniMax 视觉能力组件：Coding Plan 图片理解 + 平台图像生成。"""

    name = "minimax"
    capabilities = frozenset({"understand", "image_gen"})

    def is_configured(self, capability: str) -> bool:
        try:
            client = _client()
            if capability == "understand":
                return client.coding_plan_configured
            return client.configured
        except Exception:
            return False

    def status_details(self, capability: str) -> Dict[str, Any]:
        return {}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        if capability == "understand":
            return await self._understand(**kwargs)
        if capability == "image_gen":
            return await self._image_gen(**kwargs)
        raise CapabilityNotSupported(f"minimax 组件不支持能力 '{capability}'")

    async def _understand(self, *, image_path: str, prompt: str) -> Dict[str, Any]:
        from entities._sdk import is_video_path
        from entities.minimax.client import image_to_data_url
        if is_video_path(image_path):
            # understand_image 仅接受图片格式，视频不在本组件能力内
            raise CapabilityNotSupported("minimax 组件不支持视频识别")
        client = _client()
        if not client.coding_plan_configured:
            raise ProviderUnavailable("MiniMax Coding Plan 未配置凭据")
        # 本地路径先做沙箱校验再转 data URL
        if not image_path.startswith(("http://", "https://", "data:image/")):
            from entities._sdk import resolve_workspace_path
            image_path = resolve_workspace_path(image_path)
        data_url = await image_to_data_url(image_path)
        content = await client.coding_plan_understand_image(prompt, data_url)
        return {"description": content, "model": "minimax-coding-plan"}

    async def _image_gen(
        self,
        *,
        prompt: str,
        image_size: str,
        num_inference_steps: int,
        n: int,
        reference_image: str,
    ) -> Dict[str, Any]:
        client = _client()
        if not client.configured:
            raise ProviderUnavailable("MiniMax 未配置 api_key")
        aspect_ratio = _to_aspect_ratio(image_size)
        if reference_image:
            if not reference_image.startswith(("http://", "https://", "data:image/")):
                import base64
                import mimetypes

                from entities._sdk import resolve_workspace_path
                resolved = resolve_workspace_path(reference_image)
                if not os.path.exists(resolved):
                    raise FileNotFoundError(f"参考图片不存在: {reference_image}")
                mime = mimetypes.guess_type(os.path.basename(resolved))[0] or "image/jpeg"
                with open(resolved, "rb") as f:
                    reference_image = f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"
            image_results = await client.image_to_image(
                prompt, reference_image, aspect_ratio=aspect_ratio, n=n,
            )
        else:
            image_results = await client.generate_image(
                prompt, aspect_ratio=aspect_ratio, n=n,
            )
        if not image_results:
            raise RuntimeError("未返回结果")
        return {"image_results": image_results, "model": "minimax-direct"}


# ==================================================================
# 声音能力组件
# ==================================================================

class MiniMaxSoundProvider:
    """MiniMax 声音能力组件：一次性语音合成 + 音色管理。"""

    name = "minimax"
    capabilities = frozenset({"tts", "voice_mgmt"})

    def is_configured(self, capability: str) -> bool:
        try:
            return _client().configured
        except Exception:
            return False

    def status_details(self, capability: str) -> Dict[str, Any]:
        return {}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        if capability == "tts":
            return await self._tts(**kwargs)
        if capability == "voice_mgmt":
            return await self._voice_mgmt(**kwargs)
        raise CapabilityNotSupported(f"minimax 组件不支持能力 '{capability}'")

    async def _tts(
        self,
        *,
        text: str,
        voice: str,
        references: Optional[List[Dict[str, str]]],
        emotion: str,
        speed: float,
        pitch: int,
        language_boost: str,
    ) -> Dict[str, Any]:
        if references:
            raise CapabilityNotSupported("声音克隆参考音频仅 models 链（OpenAI 风格协议）支持")
        client = _client()
        if not client.configured:
            raise ProviderUnavailable("MiniMax 未配置 api_key")
        audio_bytes = await client.text_to_speech(
            text,
            voice_id=voice,
            speed=speed or 1.0,
            pitch=pitch,
            emotion=emotion,
            language_boost=language_boost,
        )
        return {"audio_bytes": audio_bytes, "model": "minimax-direct"}

    async def _voice_mgmt(self, *, op: str, **kwargs: Any) -> Dict[str, Any]:
        client = _client()
        if not client.configured:
            raise ProviderUnavailable("MiniMax 未配置 api_key")
        if op == "clone":
            with open(kwargs["resolved"], "rb") as f:
                file_data = f.read()
            file_id = await client.upload_file(
                file_data, os.path.basename(kwargs["resolved"]), purpose="voice_clone",
            )
            result = await client.voice_clone(
                file_id, kwargs["voice_id"], preview_text=kwargs.get("preview_text", ""),
            )
            return {
                "voice_id": result["voice_id"],
                "file_id": file_id,
                "has_demo": bool(result.get("demo_audio")),
                "model": "minimax-direct",
            }
        if op == "design":
            result = await client.voice_design(
                kwargs["prompt"], kwargs["preview_text"], voice_id=kwargs.get("voice_id", ""),
            )
            return {"voice_id": result.get("voice_id", ""), "model": "minimax-direct"}
        if op == "list":
            return await client.get_voices(kwargs.get("voice_type") or "all")
        if op == "delete":
            return await client.delete_voice(kwargs["voice_id"], kwargs.get("voice_type") or "voice_cloning")
        raise CapabilityNotSupported(f"未知音色管理操作: {op}")


# ==================================================================
# 检索组件
# ==================================================================

class MiniMaxSearchProvider(RetrievalProvider):
    """MiniMax Coding Plan 网页检索（订阅配额，不计 API 调用费）。

    凭据解析链：凭据中心 minimax_coding_plan → minimax → MINIMAX_API_KEY 环境变量。
    """

    name = "minimax"
    display_name = "MiniMax"
    description = "MiniMax Coding Plan 联网检索（订阅配额，不计 API 调用费）"
    key_hint = "凭据中心（声音页组件凭据）配置 minimax_coding_plan，或 minimax 平台 Key"

    def credential(self) -> Tuple[str, str]:
        from entities._sdk import get_provider_key
        for name in ("minimax_coding_plan", "minimax"):
            value = get_provider_key(name)
            if value:
                return value, SOURCE_CONFIG
        env_key = os.environ.get("MINIMAX_API_KEY", "").strip()
        return (env_key, SOURCE_ENV) if env_key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        from entities._sdk import set_provider_key
        set_provider_key("minimax_coding_plan", "api_key", api_key)

    def search(self, query: str, max_results: int) -> Dict[str, Any]:
        api_key, _source = self.credential()
        if not api_key:
            raise RuntimeError(f"MiniMax Coding Plan 未配置凭据（{self.key_hint}）")
        from entities.minimax.client import MiniMaxClient, normalize_search_results
        client = MiniMaxClient()
        data = run_coro_sync(client.coding_plan_search(query, timeout=_SEARCH_TIMEOUT, api_key=api_key))
        return normalize_search_results(data, query, max_results)

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        from entities.minimax.client import minimax_error_response
        return minimax_error_response(
            exc, action,
            hint=hint or "检查网络连通性，以及 MiniMax Coding Plan 凭据配置",
        )


# ==================================================================
# 流式 TTS 组件
# ==================================================================

class MiniMaxTtsProvider:
    """MiniMax 流式 TTS（t2a_v2 stream=true，PCM 直出）。"""

    name = "minimax"
    priority = 10

    async def check_available(self) -> bool:
        return _client().configured

    def stream_synthesize(
        self, text: str, *, voice: str = "", sample_rate: int = 24000,
    ) -> TtsStream:
        return TtsStream(self._stream(text, voice=voice, sample_rate=sample_rate),
                         sample_rate)

    async def _stream(
        self, text: str, *, voice: str, sample_rate: int,
    ) -> AsyncIterator[bytes]:
        import json

        from entities.minimax.client import get_config
        client = _client()
        model = get_config("default_tts_model", "speech-2.8-hd")
        voice_id = voice or "male-qn-qingse"
        payload: Dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": True,
            "voice_setting": {"voice_id": voice_id, "speed": 1.0, "vol": 1.0, "pitch": 0},
            "audio_setting": {"format": "pcm", "sample_rate": sample_rate},
        }
        url = "https://api.minimaxi.com/v1/t2a_v2"
        async with client._http_client(timeout=60.0) as http:
            async with http.stream(
                "POST", url, headers=client._json_headers(), json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    audio_hex = (data.get("data") or {}).get("audio", "")
                    if audio_hex:
                        yield bytes.fromhex(audio_hex)
