"""实体、组件与频道共用的进程内模型调用接口。

模型看到：仅调用方显式传入的消息、媒体或判断题，不自动继承对话或执行工具。
Token：不增加提示词与判断调用；失败沿项目配置的同类型模型链回退。
缓存：不修改主对话上下文，不缓存配置与客户端，配置热更在下一次调用生效。
"""

from __future__ import annotations

from typing import Any

from agent.judgment.types import (
    ChoiceQuestion,
    JsonValue,
    JudgmentError,
    JudgmentReport,
    NoulCriteria,
    NoulQuestion,
    Question,
    ScoreQuestion,
)
from agent.llm.config import ModelType
from agent.llm.llm_client import LLMNotConfiguredError
from agent.llm.llm_manager import LLMManager, get_llm_manager
from agent.llm.model_execution import (
    ModelCallError,
    ModelResult,
    execute_candidates,
    execute_media,
    select_candidates,
    validate_timeout,
)
from agent.llm.music_adapters import MusicResult
from agent.llm.types import ChatResult, ImageContent, VideoContent

__all__ = [
    "ModelAPI", "get_model_api", "ModelType", "ModelResult", "ModelCallError", "LLMNotConfiguredError",
    "ChatResult", "ImageContent", "VideoContent", "MusicResult",
    "ChoiceQuestion", "NoulQuestion", "NoulCriteria", "ScoreQuestion", "Question",
    "JsonValue", "JudgmentReport", "JudgmentError",
]


class ModelAPI:
    """按类型调用已配置模型；model_id 非空时固定模型，不隐式切换。"""

    def __init__(self, manager: LLMManager | None = None) -> None:
        self._manager = manager

    @property
    def manager(self) -> LLMManager:
        """读取运行时管理器，未注入时复用项目单例。"""
        return self._manager if self._manager is not None else get_llm_manager()

    def list_models(self, model_type: ModelType | str) -> list[str]:
        """列出已启用的模型配置 ID，顺序与 Web 模型优先级一致。"""
        return [client.config.name for client in self.manager.get_all_by_type(ModelType(model_type))]

    async def chat(
        self, messages: list[dict[str, Any]], *, model_id: str = "",
        model_type: ModelType | str = ModelType.CHAT,
        options: dict[str, Any] | None = None, tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None, max_retries: int = 2, timeout: float = 120.0,
        purpose: str = "sdk", stream: bool = False,
    ) -> ChatResult:
        """复用聊天重试、协议转换、流式聚合与用量记账；工具调用仅作为结果返回。"""
        validate_timeout(timeout)
        kind = ModelType(model_type)
        if kind not in (ModelType.CHAT, ModelType.VISION):
            raise ValueError("chat 仅支持 chat 或 vision 类型")
        manager = self.manager
        needs_vision = manager._messages_contain_images(messages)
        candidates = select_candidates(
            manager, kind, model_id=model_id,
            accepts=lambda client: (not tools or client.config.supports_tools)
            and (not needs_vision or client.config.supports_vision),
        )
        return await manager.chat_with_fallback(
            messages, client=candidates[0], options=options, tools=tools,
            tool_choice=tool_choice, max_retries=max_retries, timeout=timeout,
            purpose=purpose, stream=stream, model_type=kind, allow_fallback=not model_id,
        )

    async def describe_images(
        self, images: list[ImageContent], prompt: str = "请简要描述这些图片的内容。", *,
        model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[str]:
        """按视觉优先级识别图片，返回描述与实际模型；未传时限时遵循模型空闲超时。"""
        if not images:
            raise ValueError("images 不能为空")
        return await execute_candidates(
            self.manager, ModelType.VISION,
            lambda client: client.describe_images(images, prompt=prompt),
            model_id=model_id, timeout=timeout,
            accepts=lambda client: all(
                client.config.supports_url_vision if image.is_url
                else client.config.supports_base64_vision for image in images
            ),
        )

    async def describe_video(
        self, video: VideoContent, prompt: str = "请简要描述这个视频的内容。", *,
        model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[str]:
        """仅向声明支持视频理解的视觉模型发送视频。"""
        return await execute_candidates(
            self.manager, ModelType.VISION,
            lambda client: client.describe_video(video, prompt=prompt),
            model_id=model_id, timeout=timeout,
            accepts=lambda client: client.config.supports_video,
        )

    async def embed(
        self, texts: list[str], *, model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[list[list[float]]]:
        """批量文本向量化；持久化索引必须固定 model_id，禁止混合不同模型的向量空间。"""
        if not texts:
            raise ValueError("texts 不能为空")
        return await execute_candidates(
            self.manager, ModelType.EMBEDDING, lambda client: client.embed(texts),
            model_id=model_id, timeout=timeout,
        )

    async def embed_multimodal(
        self, contents: list[dict[str, Any]], *, model_id: str = "",
        timeout: float | None = None,
    ) -> ModelResult[list[list[float]]]:
        """使用支持多模态嵌入的模型；结果带模型身份，供调用方隔离索引。"""
        if not contents:
            raise ValueError("contents 不能为空")
        return await execute_candidates(
            self.manager, ModelType.EMBEDDING, lambda client: client.embed_multimodal(contents),
            model_id=model_id, timeout=timeout,
            accepts=lambda client: client.supports_multimodal_embedding,
        )

    async def transcribe(
        self, audio_data: bytes, *, file_name: str = "audio.mp3", mime_type: str = "",
        model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[str]:
        """调用项目 ASR 模型链，将音频字节转为文本。"""
        return await execute_media(
            self.manager, ModelType.ASR,
            lambda model, client: client.transcribe(
                audio_data, model=model, file_name=file_name, mime_type=mime_type,
            ), model_id=model_id, timeout=timeout,
        )

    async def text_to_speech(
        self, text: str, *, voice: str = "", response_format: str = "mp3",
        references: list[dict[str, str]] | None = None, emotion: str = "",
        speed: float | None = None, vol: float | None = None, pitch: int | None = None,
        language_boost: str = "", model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[bytes]:
        """调用项目 TTS 模型链，返回音频字节。"""
        return await execute_media(
            self.manager, ModelType.TTS,
            lambda model, client: client.text_to_speech(
                text, model=model, voice=voice, response_format=response_format,
                references=references, emotion=emotion, speed=speed, vol=vol,
                pitch=pitch, language_boost=language_boost,
            ), model_id=model_id, timeout=timeout,
        )

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int = 5,
        model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[list[dict[str, Any]]]:
        """调用项目重排序模型链，返回相关性排序结果。"""
        return await execute_media(
            self.manager, ModelType.RERANK,
            lambda model, client: client.rerank(query, documents, model=model, top_n=top_n),
            model_id=model_id, timeout=timeout,
        )

    async def generate_image(
        self, prompt: str, *, image_size: str = "1024x1024", num_inference_steps: int = 20,
        cfg: float | None = None, model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[list[str]]:
        """按图片生成模型优先级返回图片地址。"""
        return await execute_media(
            self.manager, ModelType.IMAGE_GEN,
            lambda model, client: client.generate_image(
                prompt, model=model, image_size=image_size,
                num_inference_steps=num_inference_steps, cfg=cfg,
            ), model_id=model_id, timeout=timeout,
        )

    async def edit_image(
        self, prompt: str, *, image_path: str, num_inference_steps: int = 20,
        cfg: float = 4.0, model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[list[str]]:
        """按图片编辑模型优先级返回编辑结果地址。"""
        return await execute_media(
            self.manager, ModelType.IMAGE_EDIT,
            lambda model, client: client.edit_image(
                prompt, model=model, image_path=image_path,
                num_inference_steps=num_inference_steps, cfg=cfg,
            ), model_id=model_id, timeout=timeout,
        )

    async def generate_video(
        self, prompt: str, *, first_frame_image: str = "", last_frame_image: str = "",
        subject_reference: list[str] | None = None, duration: int | None = None,
        resolution: str = "", ratio: str = "", prompt_optimizer: bool | None = None,
        fast_pretreatment: bool | None = None, aigc_watermark: bool | None = None,
        model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[str]:
        """提交视频生成并等待结果；轮询与协议差异由媒体客户端处理。"""
        return await execute_media(
            self.manager, ModelType.VIDEO,
            lambda model, client: client.generate_video(
                prompt, model=model, first_frame_image=first_frame_image,
                last_frame_image=last_frame_image, subject_reference=subject_reference,
                duration=duration, resolution=resolution, ratio=ratio,
                prompt_optimizer=prompt_optimizer, fast_pretreatment=fast_pretreatment,
                aigc_watermark=aigc_watermark,
            ), model_id=model_id, timeout=timeout,
        )

    async def generate_music(
        self, *, prompt: str = "", lyrics: str = "", is_instrumental: bool = False,
        audio_url: str = "", audio_base64: str = "", cover_feature_id: str = "",
        model_id: str = "", timeout: float | None = None,
    ) -> ModelResult[MusicResult]:
        """按音乐模型优先级生成歌曲、纯音乐或翻唱。"""
        return await execute_media(
            self.manager, ModelType.MUSIC,
            lambda model, client: client.generate_music(
                model=model, prompt=prompt, lyrics=lyrics, is_instrumental=is_instrumental,
                audio_url=audio_url, audio_base64=audio_base64, cover_feature_id=cover_feature_id,
            ), model_id=model_id, timeout=timeout,
        )

    async def judge(
        self, state: JsonValue, questions: dict[str, Question], *,
        model_id: str = "", native_only: bool = False, timeout: float | None = None,
    ) -> JudgmentReport:
        """批量结构化判断；复用 Jev 候选、健康状态与普通模型回退配置。"""
        from agent.judgment.engine import get_judgment_engine

        validate_timeout(timeout)
        return await get_judgment_engine().judge(
            state, questions, model_id=model_id, native_only=native_only, timeout=timeout,
        )


_api = ModelAPI()


def get_model_api() -> ModelAPI:
    """获取无会话状态的模型调用接口，不创建独立模型池。"""
    return _api
