"""视觉生成工具 — 图片/视频理解、图像生成/编辑、视频生成与视觉能力配置。

接口层：参数归一 → 沙箱校验 → get_visual_router() 按优先级链路由；
产物（图片/视频）统一落盘 workspace/uploads/ 并返回相对路径。

provider 参数：auto（默认，按配置优先级链自动路由+失败降级）或指定
提供者名；可用值与能力优先级经 vision_config(action="providers") 查看。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from agent.utils import workspace as ws
from agent.vision.capabilities import (
    VISUAL_CAPABILITIES,
    apply_style,
    get_default_param,
    get_visual_router,
)
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import deferred_tool

_group = "vision"


def _dumps(out: Dict[str, Any]) -> str:
    return json.dumps(out, ensure_ascii=False)


def _check_provider(provider: str) -> Optional[str]:
    """校验 provider 参数合法性，非法返回错误 JSON。"""
    names = get_visual_router().names()
    if provider and provider != "auto" and provider not in names:
        return tool_error(
            f"未知提供者: {provider}",
            cause=ErrorCause.PARAM, retryable=False,
            hint=f"可选: auto / {' / '.join(names)}",
        )
    return None


def _main_model_supports_vision() -> bool:
    """主对话模型是否具备视觉能力（与 think_loop 图片注入门控同源：
    运行时未就绪返回 None → False 走识别链）。"""
    from agent.runtime.singleton import get_runtime
    rt = get_runtime()
    client = getattr(rt, "llm", None) if rt is not None else None
    return bool(getattr(getattr(client, "config", None), "supports_vision", False))


# ==================================================================
# 图片/视频理解
# ==================================================================

async def _recognize_visual(media_path: str, prompt: str, provider: str, kwargs: Dict[str, str]) -> str:
    """recognize_image / recognize_video 共用的识别流程：参数归一 → 沙箱校验 → 直注或识别链。"""
    if not media_path:
        media_path = (
            kwargs.get("media_file", "")
            or kwargs.get("image_source", "")
            or kwargs.get("video_source", "")
            or kwargs.get("path", "")
            or kwargs.get("file_path", "")
            or kwargs.get("url", "")
        )
    if media_path.startswith("image:"):
        return tool_error(f"路径不需要 'image:' 前缀，请直接传路径: {media_path[6:]}",
                          cause=ErrorCause.PARAM, retryable=False)
    if not media_path:
        return tool_error("未提供图片/视频路径或 URL",
                          cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    provider = provider or "auto"

    from agent.llm.image_utils import is_video_path
    is_video = is_video_path(media_path)
    is_remote = media_path.startswith(("http://", "https://", "data:image/"))
    if not is_remote:
        try:
            resolved = ws.resolve_workspace_path(media_path)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved):
            return tool_error(f"文件不存在: {media_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=resolved)
        media_path = resolved

    desc_prompt = prompt or (
        "请简要描述这个视频的内容。" if is_video else "请简要描述这张图片的内容。"
    )
    # 主模型有视觉能力时跳过识别链，直接按 _multimodal 约定回注原图——
    # 省一次视觉模型调用；视频与远程 URL 无法注入本地 block，仍走识别链
    if not is_video and not is_remote and provider == "auto" and _main_model_supports_vision():
        return _dumps({
            "success": True,
            "image_path": media_path,
            "_multimodal": True,
            "text": f"[系统] 图片已附上，请直接查看并按调用要求分析（{desc_prompt}）。",
            "images": [media_path],
        })
    try:
        out = await get_visual_router().run(
            "understand", "视频识别" if is_video else "图片识别", provider=provider,
            image_path=media_path, prompt=desc_prompt,
        )
        if out.get("success"):
            out["image_path"] = media_path
        return _dumps(out)
    except Exception as e:
        return error_from_exception(e, action="识别视频" if is_video else "识别图片")


@deferred_tool(name="recognize_image", group=_group, tags=["always", "media:image"], timeout=300.0)
async def recognize_image(image_path: str = "", prompt: str = "", provider: str = "auto", **kwargs: str) -> str:
    """识别/分析图片内容（视频请用 recognize_video）。支持本地文件路径或 URL。

    主模型具备视觉能力时：本地图片不调用识别链，按 _multimodal 约定把原图
    直接注入工具链尾部（动态区，不动前缀缓存），主模型亲自看图分析，
    省一次视觉模型调用；显式指定 provider 时强制走识别链。
    主模型无视觉能力时：经视觉模型链识别，返回文字描述。

    Args:
        image_path: 图片的绝对路径或 URL
        prompt: 可选的分析提示，如"描述图片中的文字"
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
            （可用值见 vision_config(action="providers")，随组件增删变化）
    """
    return await _recognize_visual(image_path, prompt, provider, kwargs)


@deferred_tool(name="recognize_video", group=_group, tags=["always", "media:video"], timeout=300.0)
async def recognize_video(video_path: str = "", prompt: str = "", provider: str = "auto", **kwargs: str) -> str:
    """识别/分析视频内容（画面理解）。支持本地文件路径或 URL。

    经视觉模型链把视频发送给声明 supports_video 的模型识别（未声明
    的模型不投送），返回文字描述。与 recognize_image 的区别：
    视频无法直注主模型上下文，始终走识别链。

    Args:
        video_path: 视频的绝对路径或 URL
        prompt: 可选的分析提示，如"总结视频里发生的事情"
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    return await _recognize_visual(video_path, prompt, provider, kwargs)


# ==================================================================
# 图片生成 / 编辑
# ==================================================================

@deferred_tool(name="generate_image", group=_group, tags=["always", "media:image_gen"], timeout=300.0)
async def generate_image(
    prompt: str,
    image_size: str = "",
    n: int = 1,
    num_inference_steps: int = 20,
    style: str = "",
    reference_image: str = "",
    provider: str = "auto",
) -> str:
    """根据文字描述生成图片（文生图），生成结果保存到本地并返回文件路径。

    reference_image 非空时转为人物参考图生图（保持人物特征，仅支持的组件提供者）。

    Args:
        prompt: 图片内容的文字描述
        image_size: 图片尺寸，留空用默认配置；支持像素格式 "1024x1024"、"1664x928"
            或比例格式 "1:1"/"16:9"/"9:16"（组件提供者按最近比例映射）
        n: 生成数量 1~9（仅支持的组件提供者生效，models 链由模型决定）
        num_inference_steps: 推理步数，默认 20（仅 models 链生效），越高越精细但更慢
        style: 可选风格预设名（vision_style_presets 配置）或自定义风格描述
        reference_image: 人物参考照片的本地路径或 URL（非空=人物参考图生图）
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    err = _check_provider(provider)
    if err:
        return err
    prompt = apply_style(prompt, style)
    image_size = image_size or str(get_default_param("image_size", "1024x1024"))
    n = min(max(1, int(n)), 9)

    out = await get_visual_router().run(
        "image_gen", "图片生成", provider=provider or "auto",
        prompt=prompt, image_size=image_size, n=n,
        num_inference_steps=num_inference_steps, reference_image=reference_image,
    )
    if out.get("success") and isinstance(out.get("image_results"), list):
        out["file_paths"] = await ws.save_images(out.pop("image_results"))
        if n > 1 and out.get("provider") == "models":
            out["note"] = "n 参数仅部分组件提供者生效，models 链生成数量由模型决定"
    return _dumps(out)


@deferred_tool(name="edit_image", group=_group, tags=["always", "media:image_edit"], timeout=300.0)
async def edit_image(
    image_path: str,
    prompt: str,
    num_inference_steps: int = 20,
    provider: str = "auto",
) -> str:
    """对已有图片按文字指令进行编辑/修改，返回编辑后图片的文件路径。

    Args:
        image_path: 要编辑的图片，本地路径或 URL
        prompt: 编辑指令，描述希望如何修改图片
        num_inference_steps: 推理步数，默认 20
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    err = _check_provider(provider)
    if err:
        return err
    if image_path.startswith(("http://", "https://")):
        resolved_image = image_path
    else:
        try:
            resolved_image = ws.resolve_workspace_path(image_path)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved_image):
            return tool_error(f"图片不存在: {image_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)

    out = await get_visual_router().run(
        "image_edit", "图片编辑", provider=provider or "auto",
        image_path=resolved_image, prompt=prompt, num_inference_steps=num_inference_steps,
    )
    if out.get("success") and isinstance(out.get("image_results"), list):
        out["file_paths"] = await ws.save_images(out.pop("image_results"))
        out["source_image"] = image_path
    return _dumps(out)


# ==================================================================
# 视频生成与任务管理
# ==================================================================

@deferred_tool(name="generate_video", group=_group, tags=["always", "media:video"], timeout=620.0)
async def generate_video(
    prompt: str,
    image_url: str = "",
    first_frame_image: str = "",
    last_frame_image: str = "",
    subject_reference: str = "",
    duration: int = 0,
    resolution: str = "",
    ratio: str = "",
    style: str = "",
    provider: str = "auto",
) -> str:
    """根据文字描述生成视频，结果下载到本地并返回文件路径。

    支持文生视频、图生视频（首帧/尾帧）、主体参考视频，具体能力取决于
    当前视频模型协议（部分协议仅 prompt + 首帧图）。

    Args:
        prompt: 视频内容的文字描述
        image_url: 首帧参考图（兼容参数，等同 first_frame_image），本地路径或 URL
        first_frame_image: 首帧图片，本地路径或 URL（图生视频）
        last_frame_image: 尾帧图片，本地路径或 URL（首尾帧视频）
        subject_reference: 主体参考图片，单个路径/URL 或 JSON 数组字符串
        duration: 视频时长（秒），0 表示用默认配置或模型默认
        resolution: 分辨率，留空用默认配置；如 "2K" 或 "768P"/"1080P"
        ratio: 画面比例，如 "16:9"/"9:16"（仅支持的协议生效）
        style: 可选风格预设名（vision_style_presets 配置）或自定义风格描述
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    err = _check_provider(provider)
    if err:
        return err
    prompt = apply_style(prompt, style)
    duration = duration or int(get_default_param("video_duration", 0) or 0)
    resolution = resolution or str(get_default_param("video_resolution", ""))

    try:
        first_frame = ws.to_image_value(first_frame_image or image_url) if (first_frame_image or image_url) else ""
        last_frame = ws.to_image_value(last_frame_image) if last_frame_image else ""
        subjects = [ws.to_image_value(item) for item in ws.parse_subject_reference(subject_reference)]
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                          hint="请使用工作目录（workspace）内的路径")
    except FileNotFoundError as e:
        return tool_error(str(e), cause=ErrorCause.NOT_FOUND, retryable=False)

    out = await get_visual_router().run(
        "video", "视频生成", provider=provider or "auto",
        op="generate", prompt=prompt,
        first_frame_image=first_frame, last_frame_image=last_frame,
        subject_reference=subjects, duration=duration, resolution=resolution, ratio=ratio,
    )
    if out.get("success") and out.get("video_url"):
        out["file_path"] = await ws.save_video(out["video_url"])
    return _dumps(out)


@deferred_tool(name="query_video_task", group=_group, tags=["core"], timeout=300.0)
async def query_video_task(task_id: str, download: bool = True, provider: str = "auto") -> str:
    """查询视频生成任务状态；任务成功时可下载视频到本地并返回文件路径。

    Args:
        task_id: 视频任务 ID（由创建任务响应或任务列表获得）
        download: 任务成功时是否下载视频到本地（默认是）
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    if not task_id.strip():
        return tool_error("未提供 task_id", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    from entities._sdk import coerce_bool_arg
    download = coerce_bool_arg(download, True)

    out = await get_visual_router().run(
        "video", "视频任务查询", provider=provider or "auto", op="query", task_id=task_id.strip(),
    )
    if out.get("success") and out.get("status") == "succeeded" and download and out.get("video_url"):
        out["file_path"] = await ws.save_video(out["video_url"])
    return _dumps(out)


@deferred_tool(name="list_video_tasks", group=_group, tags=["core"])
async def list_video_tasks(page_num: int = 1, page_size: int = 20, status: str = "", provider: str = "auto") -> str:
    """分页查询近 7 天的视频生成任务列表（仅支持的协议生效）。

    Args:
        page_num: 页码，从 1 开始
        page_size: 每页条数
        status: 可选状态过滤：queued/running/succeeded/failed/cancelled/expired
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await get_visual_router().run(
        "video", "视频任务列表", provider=provider or "auto",
        op="list", page_num=max(1, int(page_num)), page_size=max(1, int(page_size)), status=status.strip(),
    ))


@deferred_tool(name="cancel_video_task", group=_group, tags=["core"])
async def cancel_video_task(task_id: str, provider: str = "auto") -> str:
    """取消排队中的视频任务（不计费）或删除已终结的任务记录（仅支持的协议生效）。

    Args:
        task_id: 视频任务 ID
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    if not task_id.strip():
        return tool_error("未提供 task_id", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await get_visual_router().run(
        "video", "视频任务取消", provider=provider or "auto", op="cancel", task_id=task_id.strip(),
    ))


# ==================================================================
# 视觉能力配置管理
# ==================================================================

_SCALAR_KEYS = {
    "default_image_size": "vision_default_image_size",
    "default_video_resolution": "vision_default_video_resolution",
    "default_video_duration": "vision_default_video_duration",
}


@deferred_tool(name="vision_config", group=_group, tags=["core"])
async def vision_config(action: str = "capabilities", key: str = "", value: str = "") -> str:
    """查看视觉能力矩阵与提供者状态，或修改视觉默认参数/风格预设/优先级链。

    典型用法：
    - 规划视觉任务前先 capabilities 查当前可用能力与调用示例
    - set style_presets.<预设名> <风格描述> 维护风格预设（value 为空=删除）

    Args:
        action: capabilities（能力矩阵：工具选型+参数+示例+实时可用状态，默认）/
            providers（各提供者能力与配置状态）/ get（全部视觉配置）/ set（修改指定键）
        key: set 时必填。可选：default_image_size / default_video_resolution /
            default_video_duration / style_presets.<预设名> /
            provider_priority.<能力名>（value 为 JSON 数组如 '["models"]'，
            能力名: understand/image_gen/image_edit/video）
        value: set 时必填，配置值（provider_priority 用 JSON 数组字符串）
    """
    from core.config import ConfigManager
    from entities._sdk import save_config_value

    action = action.strip().lower() or "capabilities"
    router = get_visual_router()

    if action == "get":
        return _dumps({"success": True, "config": {
            "provider_priority": ConfigManager.get("vision_provider_priority", {}),
            "default_image_size": get_default_param("image_size", "1024x1024"),
            "default_video_resolution": get_default_param("video_resolution", ""),
            "default_video_duration": get_default_param("video_duration", 0),
            "style_presets": ConfigManager.get("vision_style_presets", {}),
        }})
    if action == "providers":
        return _dumps({"success": True, **router.status(list(VISUAL_CAPABILITIES))})
    if action == "capabilities":
        from agent.vision.guide import VISUAL_CAPABILITY_GUIDE
        matrix: Dict[str, Any] = {}
        for cap, guide in VISUAL_CAPABILITY_GUIDE.items():
            chain = router.chain(cap)
            providers_info = []
            available = False
            for name in chain:
                impl = router.get(name)
                if impl is None or cap not in impl.capabilities:
                    providers_info.append({"name": name, "configured": False, "note": "不支持该能力"})
                    continue
                try:
                    ready = impl.is_configured(cap)
                except Exception:
                    ready = False
                providers_info.append({"name": name, "configured": ready})
                available = available or ready
            matrix[cap] = {**guide, "chain": chain, "available": available, "providers": providers_info}
        return _dumps({
            "success": True,
            "capabilities": matrix,
            "hint": "available=false 的能力说明链上提供者均未配置，可用 providers 动作查看详情，"
                    "或引导主人在视觉页签/模型配置中补齐",
        })
    if action != "set":
        return tool_error(f"未知操作: {action}", cause=ErrorCause.PARAM, retryable=False,
                          hint="可选: capabilities / providers / get / set")
    if not key.strip():
        return tool_error("set 操作必须提供 key", cause=ErrorCause.PARAM, retryable=False)
    key = key.strip()

    if key in _SCALAR_KEYS:
        config_key = _SCALAR_KEYS[key]
        parsed: Any = int(value) if key == "default_video_duration" else str(value)
        save_config_value(config_key, parsed)
        return _dumps({"success": True, "key": key, "value": parsed})

    if key.startswith("style_presets."):
        name = key.split(".", 1)[1].strip()
        if not name:
            return tool_error("style_presets 键必须带预设名，如 style_presets.nekomimi_maid",
                              cause=ErrorCause.PARAM, retryable=False)
        presets = dict(ConfigManager.get("vision_style_presets", {}) or {})
        if str(value).strip():
            presets[name] = str(value)
        else:
            presets.pop(name, None)
        save_config_value("vision_style_presets", presets)
        return _dumps({"success": True, "key": key, "style_presets": presets})

    if key.startswith("provider_priority."):
        cap = key.split(".", 1)[1].strip()
        if cap not in VISUAL_CAPABILITIES:
            return tool_error(f"未知能力名: {cap}", cause=ErrorCause.PARAM, retryable=False,
                              hint=f"可选: {' / '.join(VISUAL_CAPABILITIES)}")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [p.strip() for p in value.split(",") if p.strip()]
        names = router.names()
        unknown = [p for p in parsed if isinstance(p, str) and p not in names]
        if not isinstance(parsed, list) or unknown:
            return tool_error(
                f"provider_priority 值非法: {value}",
                cause=ErrorCause.PARAM, retryable=False,
                hint=f"提供者可选: {' / '.join(names)}，示例 '[\"models\"]'",
            )
        priority = dict(ConfigManager.get("vision_provider_priority", {}) or {})
        priority[cap] = list(parsed)
        save_config_value("vision_provider_priority", priority)
        return _dumps({"success": True, "key": key, "chain": router.chain(cap)})

    return tool_error(f"不支持的配置键: {key}", cause=ErrorCause.PARAM, retryable=False,
                      hint="可选: default_image_size / default_video_resolution / "
                           "default_video_duration / style_presets.<名> / provider_priority.<能力>")
