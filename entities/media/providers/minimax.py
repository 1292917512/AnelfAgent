"""minimax provider：桥接 entities/minimax 直连模块（可插拔）。

凭据来自 entities/minimax/config.json：api_key（平台按量，tts/音色/图片）
与 coding_plan_api_key（Token Plan 订阅，vision 图片理解）独立解析。
模块被移除或凭据未配置时，路由器自动跳过本 provider。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import (
    CAP_IMAGE_GEN,
    CAP_TTS,
    CAP_VISION,
    CAP_VOICE_MGMT,
    CapabilityNotSupported,
    MediaProvider,
    ProviderUnavailable,
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


def _client() -> Any:
    from entities.minimax.client import MiniMaxClient
    return MiniMaxClient()


def _to_aspect_ratio(image_size: str) -> str:
    """统一 size 参数转 MiniMax 比例：比例格式直通，像素格式映射最近比例。"""
    size = image_size.strip()
    if ":" in size:
        return size
    return _SIZE_TO_RATIO.get(size, "1:1")


class MiniMaxProvider(MediaProvider):
    """MiniMax 直连模块 provider。"""

    name = "minimax"
    capabilities = frozenset({CAP_VISION, CAP_TTS, CAP_VOICE_MGMT, CAP_IMAGE_GEN})

    def is_configured(self, capability: str) -> bool:
        try:
            client = _client()
            if capability == CAP_VISION:
                return client.coding_plan_configured
            return client.configured
        except Exception:
            return False

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        if capability == CAP_VISION:
            return await self._vision(**kwargs)
        if capability == CAP_TTS:
            return await self._tts(**kwargs)
        if capability == CAP_VOICE_MGMT:
            return await self._voice_mgmt(**kwargs)
        if capability == CAP_IMAGE_GEN:
            return await self._image_gen(**kwargs)
        raise CapabilityNotSupported(f"minimax provider 不支持能力 '{capability}'")

    async def _vision(self, *, image_path: str, prompt: str) -> Dict[str, Any]:
        from entities.minimax.client import image_to_data_url
        client = _client()
        if not client.coding_plan_configured:
            raise ProviderUnavailable("MiniMax Coding Plan 未配置凭据")
        # 本地路径先做沙箱校验再转 data URL
        if not image_path.startswith(("http://", "https://", "data:image/")):
            from entities.media.utils import resolve_workspace_path
            image_path = resolve_workspace_path(image_path)
        data_url = await image_to_data_url(image_path)
        content = await client.coding_plan_understand_image(prompt, data_url)
        return {"description": content, "model": "minimax-coding-plan"}

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
            raise CapabilityNotSupported("声音克隆参考音频仅 models provider（OpenAI 风格协议）支持")
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
            import os
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
                import os

                from entities.media.utils import resolve_workspace_path
                resolved = resolve_workspace_path(reference_image)
                if not os.path.exists(resolved):
                    raise FileNotFoundError(f"参考图片不存在: {reference_image}")
                import base64
                import mimetypes
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
