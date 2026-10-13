"""视觉能力路由 — 图片/视频理解、图像生成/编辑、视频生成的提供者分发。

能力集合：
- understand：图片/视频内容理解（视觉模型链；视频仅投送声明 supports_video 的模型）
- image_gen：文生图 / 参考图生图
- image_edit：按文字指令编辑图片
- video：文/图生视频与任务管理（generate/query/list/cancel）

内部模型利用：内置 ``models`` 提供者桥接模型配置（llm_clients.json）中
vision/image_gen/image_edit/video 类型的模型优先级链；第三方组件经
entities._sdk.register_visual_provider 挂入同一路由（即插即用）。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from agent.capabilities import (
    CapabilityNotSupported,
    CapabilityRouter,
    ProviderChainError,
    ProviderUnavailable,
)
from core.log import log

CAP_UNDERSTAND = "understand"
CAP_IMAGE_GEN = "image_gen"
CAP_IMAGE_EDIT = "image_edit"
CAP_VIDEO = "video"

VISUAL_CAPABILITIES = (CAP_UNDERSTAND, CAP_IMAGE_GEN, CAP_IMAGE_EDIT, CAP_VIDEO)

# 能力 → iter_media_for_type 的模型类型
_CAPABILITY_MODEL_TYPE = {
    CAP_IMAGE_GEN: "image_gen",
    CAP_IMAGE_EDIT: "image_edit",
    CAP_VIDEO: "video",
}

_LOG_TAG = "视觉"


def _mgr() -> Any:
    from agent.llm import get_llm_manager
    return get_llm_manager()


class ModelsVisualProvider:
    """内部模型链提供者：能力路由到 llm_clients.json 对应类型的模型优先级链。"""

    name = "models"
    capabilities = frozenset({CAP_UNDERSTAND, CAP_IMAGE_GEN, CAP_IMAGE_EDIT, CAP_VIDEO})

    def is_configured(self, capability: str) -> bool:
        try:
            if capability == CAP_UNDERSTAND:
                from agent.llm.llm_manager import ModelType
                return bool(_mgr().get_all_by_type(ModelType.VISION))
            model_type = _CAPABILITY_MODEL_TYPE.get(capability, "")
            return bool(model_type) and bool(_mgr().iter_media_for_type(model_type))
        except Exception as e:
            log(f"models 提供者可用性检查失败: {e}", "DEBUG", tag=_LOG_TAG)
            return False

    def status_details(self, capability: str) -> Dict[str, Any]:
        """understand 能力附加视频理解模型清单（supports_video 声明模型）。"""
        if capability != CAP_UNDERSTAND:
            return {}
        try:
            from agent.llm.llm_manager import ModelType
            video_models = [
                c.config.name
                for c in _mgr().get_all_by_type(ModelType.VISION)
                if getattr(c.config, "supports_video", False)
            ]
            return {"video_models": video_models}
        except Exception as e:
            log(f"models 提供者视频模型清单获取失败: {e}", "DEBUG", tag=_LOG_TAG)
            return {}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        if capability == CAP_UNDERSTAND:
            return await self._run_understand(**kwargs)
        model_type = _CAPABILITY_MODEL_TYPE.get(capability)
        if not model_type:
            raise CapabilityNotSupported(f"models 提供者不支持能力 '{capability}'")
        dispatch: Dict[str, Callable[..., Awaitable[Any]]] = {
            CAP_IMAGE_GEN: self._image_gen,
            CAP_IMAGE_EDIT: self._image_edit,
            CAP_VIDEO: self._video,
        }
        handler = dispatch.get(capability)
        if handler is None:
            raise CapabilityNotSupported(f"models 提供者不支持能力 '{capability}'")
        return await self._with_model_fallback(model_type, handler, kwargs)

    # ------------------------------------------------------------------
    # 模型链回退骨架
    # ------------------------------------------------------------------

    async def _with_model_fallback(
        self,
        model_type: str,
        handler: Callable[..., Awaitable[Any]],
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        from agent.llm.config import ModelType
        from agent.llm.llm_client import LLMNotConfiguredError
        from agent.llm.model_execution import ModelCallError, execute_media

        try:
            result = await execute_media(
                _mgr(), ModelType(model_type),
                lambda model, client: handler(model, client, **kwargs),
            )
        except LLMNotConfiguredError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        except ModelCallError as exc:
            raise ProviderChainError(str(exc), exc.errors) from exc
        output: Dict[str, Any] = result.value
        output.setdefault("model", result.model)
        return output

    # ------------------------------------------------------------------
    # 图片/视频理解
    # ------------------------------------------------------------------

    async def _run_understand(self, image_path: str, prompt: str) -> Dict[str, Any]:
        from agent.llm.image_utils import is_video_path
        if is_video_path(image_path):
            return await self._run_video_understand(image_path, prompt)

        from agent.llm.image_utils import (
            download_image_to_base64,
            load_image_from_path,
            optimize_for_vision,
        )
        from agent.llm.llm_client import LLMNotConfiguredError
        from agent.llm.model_api import ModelAPI

        models = ModelAPI(_mgr())
        if not models.list_models("vision"):
            raise ProviderUnavailable("未配置视觉模型")
        if image_path.startswith(("http://", "https://")):
            img = await download_image_to_base64(image_path)
            if not img:
                raise RuntimeError(f"无法下载图片（链接可能已过期）: {image_path[:100]}")
        else:
            img = load_image_from_path(image_path)
        try:
            result = await models.describe_images([optimize_for_vision(img)], prompt=prompt)
        except LLMNotConfiguredError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        return {"description": result.value, "model": result.model_id}

    async def _run_video_understand(self, video_path: str, prompt: str) -> Dict[str, Any]:
        """下载或读取视频后交给统一模型接口，仅使用支持视频理解的视觉候选。"""
        from agent.llm.image_utils import download_video_to_base64, load_video_from_path
        from agent.llm.llm_client import LLMNotConfiguredError
        from agent.llm.llm_manager import ModelType
        from agent.llm.model_api import ModelAPI

        manager = _mgr()
        if not any(client.config.supports_video for client in manager.get_all_by_type(ModelType.VISION)):
            raise ProviderUnavailable("未配置支持视频理解的模型，请在模型配置中开启 supports_video")
        if video_path.startswith(("http://", "https://")):
            video = await download_video_to_base64(video_path)
            if not video:
                raise RuntimeError(f"无法下载视频（链接可能已过期）: {video_path[:100]}")
        else:
            video = load_video_from_path(video_path)
        try:
            result = await ModelAPI(manager).describe_video(video, prompt=prompt)
        except LLMNotConfiguredError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        return {"description": result.value, "model": result.model_id}

    # ------------------------------------------------------------------
    # 图像生成 / 编辑 / 视频生成
    # ------------------------------------------------------------------

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
            raise CapabilityNotSupported("参考图生图仅部分组件提供者支持")
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


# ------------------------------------------------------------------
# 路由单例与配置
# ------------------------------------------------------------------

_router: Optional[CapabilityRouter] = None


def get_visual_router() -> CapabilityRouter:
    """视觉能力路由器单例（内部 models 提供者随首次获取注册）。"""
    global _router
    if _router is None:
        _router = CapabilityRouter(
            config_key="vision_provider_priority",
            default_chain=["models"],
            log_tag=_LOG_TAG,
        )
        _router.register(ModelsVisualProvider())
    return _router


def reset_visual_router() -> None:
    """重置路由器（测试用）。"""
    global _router
    _router = None


def get_default_param(key: str, fallback: Any = "") -> Any:
    """读取视觉默认参数（image_size / video_resolution / video_duration）。"""
    from core.config import ConfigManager
    return ConfigManager.get(f"vision_default_{key}", fallback)


def apply_style(prompt: str, style: str) -> str:
    """将风格预设拼接到提示词末尾；未命中预设时按原始风格描述拼接。"""
    if not style.strip():
        return prompt
    from core.config import ConfigManager
    presets = ConfigManager.get("vision_style_presets", {}) or {}
    suffix = presets.get(style.strip(), style.strip()) if isinstance(presets, dict) else style.strip()
    return f"{prompt}, {suffix}"
