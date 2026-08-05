"""models provider：桥接 LLMManager，使用模型配置（llm_clients.json）中的模型。

按 type_priorities 优先级逐模型回退；协议级不支持（NotImplementedError）上抛，
由路由器转交下一 provider（如 minimax 模块）。
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.log import log

from .base import (
    CAP_ASR,
    CAP_IMAGE_EDIT,
    CAP_IMAGE_GEN,
    CAP_MUSIC,
    CAP_RERANK,
    CAP_TTS,
    CAP_VIDEO,
    CAP_VISION,
    CAP_VOICE_MGMT,
    CapabilityNotSupported,
    MediaProvider,
    ModelChainError,
    ProviderUnavailable,
)

# 能力 → iter_media_for_type 的模型类型
_CAPABILITY_MODEL_TYPE = {
    CAP_ASR: "asr",
    CAP_TTS: "tts",
    CAP_VOICE_MGMT: "tts",
    CAP_MUSIC: "music",
    CAP_VIDEO: "video",
    CAP_IMAGE_GEN: "image_gen",
    CAP_IMAGE_EDIT: "image_edit",
    CAP_RERANK: "rerank",
}


def _mgr() -> Any:
    from entities._sdk import get_llm_manager
    return get_llm_manager()


class ModelsProvider(MediaProvider):
    """模型配置 provider：能力路由到 llm_clients.json 中对应类型的模型优先级链。"""

    name = "models"
    capabilities = frozenset({
        CAP_VISION, CAP_ASR, CAP_TTS, CAP_VOICE_MGMT, CAP_MUSIC,
        CAP_VIDEO, CAP_IMAGE_GEN, CAP_IMAGE_EDIT, CAP_RERANK,
    })

    def is_configured(self, capability: str) -> bool:
        try:
            if capability == CAP_VISION:
                from entities._sdk import get_model_type_enum
                return bool(_mgr().get_all_by_type(get_model_type_enum().VISION))
            model_type = _CAPABILITY_MODEL_TYPE.get(capability, "")
            return bool(model_type) and bool(_mgr().iter_media_for_type(model_type))
        except Exception as e:
            log(f"models provider 可用性检查失败: {e}", "DEBUG", tag="媒体")
            return False

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        if capability == CAP_VISION:
            return await self._run_vision(**kwargs)
        model_type = _CAPABILITY_MODEL_TYPE.get(capability)
        if not model_type:
            raise CapabilityNotSupported(f"models provider 不支持能力 '{capability}'")
        dispatch: Dict[str, Callable[..., Awaitable[Any]]] = {
            CAP_ASR: self._asr,
            CAP_TTS: self._tts,
            CAP_VOICE_MGMT: self._voice_mgmt,
            CAP_MUSIC: self._music,
            CAP_VIDEO: self._video,
            CAP_IMAGE_GEN: self._image_gen,
            CAP_IMAGE_EDIT: self._image_edit,
            CAP_RERANK: self._rerank,
        }
        handler = dispatch.get(capability)
        if handler is None:
            raise CapabilityNotSupported(f"models provider 不支持能力 '{capability}'")
        return await self._with_model_fallback(model_type, capability, handler, kwargs)

    # ------------------------------------------------------------------
    # 模型链回退骨架
    # ------------------------------------------------------------------

    async def _with_model_fallback(
        self,
        model_type: str,
        capability: str,
        handler: Callable[..., Awaitable[Any]],
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        pairs = _mgr().iter_media_for_type(model_type)
        if not pairs:
            raise ProviderUnavailable(f"未配置 {model_type} 类型模型")
        errors: Dict[str, str] = {}
        for model_name, client in pairs:
            try:
                result = await handler(model_name, client, **kwargs)
                if isinstance(result, dict):
                    result.setdefault("model", model_name)
                return result
            except NotImplementedError:
                # 协议本身不支持该操作（非单个模型故障），上抛由路由器转交下一 provider
                raise
            except Exception as exc:
                detail = str(exc).strip() or type(exc).__name__
                errors[model_name] = detail[:200]
                log(f"{capability} 模型 {model_name} 调用失败，尝试下一个: {detail}", "WARNING", tag="媒体")
                continue
        raise ModelChainError(f"所有 {model_type} 模型均调用失败", errors)

    # ------------------------------------------------------------------
    # 各能力的单模型调用
    # ------------------------------------------------------------------

    async def _run_vision(self, image_path: str, prompt: str) -> Dict[str, Any]:
        from entities._sdk import (
            download_image_to_base64,
            get_model_type_enum,
            is_video_path,
            load_image_from_path,
        )
        if is_video_path(image_path):
            return await self._run_video(image_path, prompt)
        ModelType = get_model_type_enum()

        all_vision = _mgr().get_all_by_type(ModelType.VISION)
        if not all_vision:
            raise ProviderUnavailable("未配置视觉模型")

        last_err = ""

        async def _try_candidates(candidates: List[Any], img: Any) -> Optional[Dict[str, Any]]:
            nonlocal last_err
            for vc in candidates:
                try:
                    description = await vc.describe_images([img], prompt=prompt)
                    return {"description": description, "model": vc.config.name}
                except Exception as exc:
                    last_err = str(exc)
                    log(f"视觉模型 {vc.config.name} 识别失败，尝试下一个: {last_err}", "WARNING", tag="媒体")
                    continue
            return None

        if image_path.startswith(("http://", "https://")):
            # URL 一律下载优先：端点直抓远程链接不稳定且超时不可控
            b64_img = await download_image_to_base64(image_path)
            if not b64_img:
                raise RuntimeError(f"无法下载图片（链接可能已过期）: {image_path[:100]}")
            candidates = [c for c in all_vision if c.config.supports_base64_vision] or all_vision
            result = await _try_candidates(candidates, b64_img)
            if result is not None:
                return result
        else:
            img = load_image_from_path(image_path)
            candidates = [c for c in all_vision if c.config.supports_base64_vision] or all_vision
            result = await _try_candidates(candidates, img)
            if result is not None:
                return result
        raise RuntimeError(f"所有视觉模型均调用失败: {last_err}")

    async def _run_video(self, video_path: str, prompt: str) -> Dict[str, Any]:
        """视频理解：按视觉模型优先级链逐个尝试 describe_video。"""
        from entities._sdk import (
            download_video_to_base64,
            get_model_type_enum,
            load_video_from_path,
        )
        ModelType = get_model_type_enum()

        all_vision = _mgr().get_all_by_type(ModelType.VISION)
        if not all_vision:
            raise ProviderUnavailable("未配置视觉模型")

        last_err = ""

        async def _try_candidates(candidates: List[Any], vid: Any) -> Optional[Dict[str, Any]]:
            nonlocal last_err
            for vc in candidates:
                try:
                    description = await vc.describe_video(vid, prompt=prompt)
                    return {"description": description, "model": vc.config.name}
                except Exception as exc:
                    last_err = str(exc)
                    log(f"视觉模型 {vc.config.name} 视频识别失败，尝试下一个: {last_err}", "WARNING", tag="媒体")
                    continue
            return None

        if video_path.startswith(("http://", "https://")):
            # URL 一律下载优先：端点直抓远程链接不稳定且超时不可控
            b64_vid = await download_video_to_base64(video_path)
            if not b64_vid:
                raise RuntimeError(f"无法下载视频（链接可能已过期）: {video_path[:100]}")
            result = await _try_candidates(all_vision, b64_vid)
            if result is not None:
                return result
        else:
            vid = load_video_from_path(video_path)
            result = await _try_candidates(all_vision, vid)
            if result is not None:
                return result
        raise RuntimeError(f"所有视觉模型均调用失败: {last_err}")

    async def _asr(self, model: str, client: Any, *, resolved: str, is_url: bool) -> Dict[str, Any]:
        if is_url:
            text = await client.transcribe_url(resolved, model=model)
        else:
            with open(resolved, "rb") as f:
                audio_data = f.read()
            text = await client.transcribe(
                audio_data, model=model, file_name=os.path.basename(resolved),
            )
        return {"text": text}

    async def _tts(
        self,
        model: str,
        client: Any,
        *,
        text: str,
        voice: str,
        references: Optional[List[Dict[str, str]]],
        emotion: str,
        speed: float,
        pitch: int,
        language_boost: str,
    ) -> Dict[str, Any]:
        audio_bytes = await client.text_to_speech(
            text, model=model, voice=voice, references=references,
            emotion=emotion, speed=speed or None, pitch=pitch or None,
            language_boost=language_boost,
        )
        return {"audio_bytes": audio_bytes}

    async def _voice_mgmt(self, model: str, client: Any, *, op: str, **kwargs: Any) -> Dict[str, Any]:
        if op == "clone":
            result = await client.voice_clone(
                kwargs["resolved"], voice_id=kwargs["voice_id"],
                preview_text=kwargs.get("preview_text", ""), model=model,
            )
            return {"voice_id": kwargs["voice_id"], **result}
        if op == "design":
            result = await client.voice_design(
                prompt=kwargs["prompt"], preview_text=kwargs["preview_text"],
                voice_id=kwargs.get("voice_id", ""),
            )
            out: Dict[str, Any] = {"voice_id": result.get("voice_id", "")}
            trial = result.get("trial_audio")
            if trial:
                out["trial_audio_bytes"] = trial
            return out
        if op == "list":
            return await client.list_voices(kwargs.get("voice_type") or "all")
        if op == "delete":
            return await client.delete_voice(kwargs["voice_id"], kwargs.get("voice_type") or "voice_cloning")
        raise CapabilityNotSupported(f"未知音色管理操作: {op}")

    async def _music(self, model: str, client: Any, *, op: str, **kwargs: Any) -> Dict[str, Any]:
        if op == "generate":
            result = await client.generate_music(
                model=model, prompt=kwargs.get("prompt", ""),
                lyrics=kwargs.get("lyrics", ""),
                is_instrumental=bool(kwargs.get("is_instrumental", False)),
            )
            return {"audio_bytes": result.audio, "extra_info": result.extra_info}
        if op == "lyrics":
            return await client.generate_lyrics(
                mode=kwargs.get("mode") or "write_full_song",
                prompt=kwargs.get("prompt", ""), lyrics=kwargs.get("lyrics", ""),
                title=kwargs.get("title", ""),
            )
        raise CapabilityNotSupported(f"未知音乐操作: {op}")

    async def _video(self, model: str, client: Any, *, op: str, **kwargs: Any) -> Dict[str, Any]:
        if op == "generate":
            video_url = await client.generate_video(
                kwargs["prompt"], model=model,
                first_frame_image=kwargs.get("first_frame_image", ""),
                last_frame_image=kwargs.get("last_frame_image", ""),
                subject_reference=kwargs.get("subject_reference", []),
                duration=kwargs.get("duration") or None,
                resolution=kwargs.get("resolution", ""),
                ratio=kwargs.get("ratio", ""),
            )
            if not video_url:
                raise RuntimeError("未返回视频地址")
            return {"video_url": video_url}
        if op == "query":
            return await client.query_video_task(kwargs["task_id"], model=model)
        if op == "list":
            return await client.list_video_tasks(
                model=model, page_num=kwargs.get("page_num", 1),
                page_size=kwargs.get("page_size", 20), status=kwargs.get("status", ""),
            )
        if op == "cancel":
            return await client.cancel_or_delete_video_task(kwargs["task_id"], model=model)
        raise CapabilityNotSupported(f"未知视频操作: {op}")

    async def _image_gen(
        self,
        model: str,
        client: Any,
        *,
        prompt: str,
        image_size: str,
        num_inference_steps: int,
        n: int,
        reference_image: str,
    ) -> Dict[str, Any]:
        if reference_image:
            raise CapabilityNotSupported("人物参考图生图仅 minimax provider 支持")
        image_results = await client.generate_image(
            prompt, model=model, image_size=image_size,
            num_inference_steps=num_inference_steps,
        )
        if not image_results:
            raise RuntimeError("未返回结果")
        return {"image_results": image_results}

    async def _image_edit(
        self,
        model: str,
        client: Any,
        *,
        image_path: str,
        prompt: str,
        num_inference_steps: int,
    ) -> Dict[str, Any]:
        image_results = await client.edit_image(
            prompt, model=model, image_path=image_path,
            num_inference_steps=num_inference_steps,
        )
        if not image_results:
            raise RuntimeError("未返回结果")
        return {"image_results": image_results}

    async def _rerank(self, model: str, client: Any, *, query: str, documents: List[str]) -> Dict[str, Any]:
        return {"results": await client.rerank(query, documents, model=model)}
