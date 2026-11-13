"""MiniMax 能力组件 — 视觉理解/图像生成、语音合成/音色管理、联网检索、流式 TTS。

四类组件分别接入核心路由：
- MiniMaxVisualProvider：understand（Coding Plan 图片理解）+ image_gen/image_edit → 视觉能力路由
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
    ImageResults,
    ProviderUnavailable,
    RetrievalProvider,
    TtsStream,
    run_coro_sync,
)

_SEARCH_TIMEOUT = 15.0


def _client() -> Any:
    from entities.minimax.client import MiniMaxClient
    return MiniMaxClient()


# ==================================================================
# 视觉能力组件
# ==================================================================

class MiniMaxVisualProvider:
    """MiniMax 视觉能力组件：Coding Plan 图片理解 + 平台图像生成。"""

    name = "minimax"
    capabilities = frozenset({"understand", "image_gen", "image_edit"})

    def is_configured(self, capability: str) -> bool:
        try:
            client = _client()
            if capability == "understand":
                return client.coding_plan_configured
            return client.configured
        except Exception:
            return False

    def status_details(self, capability: str) -> Dict[str, Any]:
        if capability == "image_gen":
            from .guide import image_setup_guide
            return {"setup_guide": image_setup_guide()}
        if capability == "image_edit":
            return {"setup_guide_capability": "image_gen"}
        return {}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        if capability == "understand":
            return await self._understand(**kwargs)
        if capability == "image_gen":
            return await self._image_gen(**kwargs)
        if capability == "image_edit":
            return await self._image_edit(**kwargs)
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
        self, *, prompt: str, image_size: str = "", num_inference_steps: int = 20,
        n: int = 1, reference_image: str = "", reference_images: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        client = _client()
        if not client.configured:
            raise ProviderUnavailable("MiniMax 未配置 api_key")
        images = ([reference_image] if reference_image else []) + list(reference_images or ())
        result = await client.generate_image(
            prompt, image_size=image_size, n=n, reference_images=images, options=options,
        )
        if not result:
            raise RuntimeError("未返回结果")
        details = result.details if isinstance(result, ImageResults) else {}
        return {"image_results": result, "model": "minimax-direct", **details}

    async def _image_edit(
        self, *, prompt: str, image_path: str = "", image_paths: list[str] | None = None,
        image_size: str = "", num_inference_steps: int = 20, n: int = 1,
        options: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        images = ([image_path] if image_path else []) + list(image_paths or ())
        if not images:
            raise ValueError("图片编辑至少需要一张参考图")
        return await self._image_gen(prompt=prompt, image_size=image_size, n=n,
                                     reference_images=images, options=options)


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
                produced = False
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    # 终止帧（extra_info 用量汇总）携带整句全量音频备份——
                    # 增量流已产出过音频时不产出，否则整句被播放两遍；
                    # 增量流为空时它是唯一音频来源（极短文本），照常产出
                    if data.get("extra_info") and produced:
                        break
                    audio_hex = (data.get("data") or {}).get("audio", "")
                    if audio_hex:
                        produced = True
                        yield bytes.fromhex(audio_hex)
