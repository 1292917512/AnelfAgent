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

from typing import Any, Awaitable, Callable, Dict, List, Optional

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
                # 协议本身不支持该操作（非单个模型故障），上抛由路由器转交下一提供者
                raise
            except Exception as exc:
                detail = str(exc).strip() or type(exc).__name__
                errors[model_name] = detail[:200]
                log(f"{capability} 模型 {model_name} 调用失败，尝试下一个: {detail}",
                    "WARNING", tag=_LOG_TAG)
                continue
        raise ProviderChainError(f"所有 {model_type} 模型均调用失败", errors)

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
        from agent.llm.llm_manager import ModelType
        from agent.llm.resilience import ErrorCategory, classify_llm_error

        all_vision = _mgr().get_all_by_type(ModelType.VISION)
        if not all_vision:
            raise ProviderUnavailable("未配置视觉模型")

        last_err = ""
        attempts = 0
        policy_rejects = 0

        async def _try_candidates(candidates: List[Any], img: Any) -> Optional[Dict[str, Any]]:
            nonlocal last_err, attempts, policy_rejects
            for vc in candidates:
                attempts += 1
                try:
                    description = await vc.describe_images([img], prompt=prompt)
                    return {"description": description, "model": vc.config.name}
                except Exception as exc:
                    last_err = str(exc)
                    # 内容审核是同模型确定性拒绝，但不同供应商审核尺度不同，继续回退
                    if classify_llm_error(exc).category is ErrorCategory.CONTENT_POLICY:
                        policy_rejects += 1
                    log(f"视觉模型 {vc.config.name} 识别失败，尝试下一个: {last_err}",
                        "WARNING", tag=_LOG_TAG)
                    continue
            return None

        if image_path.startswith(("http://", "https://")):
            # URL 一律下载优先：端点直抓远程链接不稳定且超时不可控
            b64_img = await download_image_to_base64(image_path)
            if not b64_img:
                raise RuntimeError(f"无法下载图片（链接可能已过期）: {image_path[:100]}")
            candidates = [c for c in all_vision if c.config.supports_base64_vision] or all_vision
            result = await _try_candidates(candidates, optimize_for_vision(b64_img))
            if result is not None:
                return result
        else:
            # 候选循环外优化一次：describe_images 内部优化对已优化图幂等直通
            img = optimize_for_vision(load_image_from_path(image_path))
            candidates = [c for c in all_vision if c.config.supports_base64_vision] or all_vision
            result = await _try_candidates(candidates, img)
            if result is not None:
                return result
        if attempts and policy_rejects == attempts:
            raise RuntimeError(f"所有视觉模型均因内容审核拒绝处理该图片: {last_err}")
        raise RuntimeError(f"所有视觉模型均调用失败: {last_err}")

    async def _run_video_understand(self, video_path: str, prompt: str) -> Dict[str, Any]:
        """视频理解：按视觉模型优先级链逐个尝试 describe_video。

        严格候选过滤：仅投送声明 supports_video 的模型——整段视频 base64 体积大，
        而多数视觉端点并不接受 video block，未声明即视为不支持，不做全链喷洒试错；
        无声明模型时直接报配置缺失，引导在模型配置中显式开启。
        """
        from agent.llm.image_utils import download_video_to_base64, load_video_from_path
        from agent.llm.llm_manager import ModelType
        from agent.llm.resilience import ErrorCategory, classify_llm_error

        all_vision = _mgr().get_all_by_type(ModelType.VISION)
        if not all_vision:
            raise ProviderUnavailable("未配置视觉模型")
        candidates = [c for c in all_vision if getattr(c.config, "supports_video", False)]
        if not candidates:
            raise ProviderUnavailable(
                "未配置支持视频理解的模型（请在模型配置中为支持视频的模型开启 supports_video）"
            )

        last_err = ""
        attempts = 0
        policy_rejects = 0

        async def _try_candidates(cands: List[Any], vid: Any) -> Optional[Dict[str, Any]]:
            nonlocal last_err, attempts, policy_rejects
            for vc in cands:
                attempts += 1
                try:
                    description = await vc.describe_video(vid, prompt=prompt)
                    return {"description": description, "model": vc.config.name}
                except Exception as exc:
                    last_err = str(exc)
                    if classify_llm_error(exc).category is ErrorCategory.CONTENT_POLICY:
                        policy_rejects += 1
                    log(f"视觉模型 {vc.config.name} 视频识别失败，尝试下一个: {last_err}",
                        "WARNING", tag=_LOG_TAG)
                    continue
            return None

        if video_path.startswith(("http://", "https://")):
            # URL 一律下载优先：端点直抓远程链接不稳定且超时不可控
            b64_vid = await download_video_to_base64(video_path)
            if not b64_vid:
                raise RuntimeError(f"无法下载视频（链接可能已过期）: {video_path[:100]}")
            result = await _try_candidates(candidates, b64_vid)
            if result is not None:
                return result
        else:
            vid = load_video_from_path(video_path)
            result = await _try_candidates(candidates, vid)
            if result is not None:
                return result
        if attempts and policy_rejects == attempts:
            raise RuntimeError(f"所有视觉模型均因内容审核拒绝处理该视频: {last_err}")
        raise RuntimeError(f"所有视觉模型均调用失败: {last_err}")

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
