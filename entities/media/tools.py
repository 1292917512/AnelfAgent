"""多模态媒体工具：图片识别、语音识别、语音合成、图片生成/编辑、视频生成、文档重排序。"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from entities._sdk import tool, entity

entity("media", "多模态媒体 - 图片识别、语音转文字、文字转语音、图片生成、图片编辑、视频生成、文档重排序")


def _get_workspace_root() -> str:
    try:
        from core.config import ConfigManager
        return ConfigManager.get("workspace_root", "workspace")
    except Exception:
        return "workspace"


def _resolve_workspace_path(path: str) -> str:
    """解析可能相对于 workspace 或 CWD 的路径。

    沙箱开启时（含绝对路径）统一经 entities/filesystem/paths.py 解析并做沙箱校验，
    越界时抛 ValueError；沙箱关闭时保持原有解析行为。
    """
    if not path:
        return ""
    from entities.filesystem import paths as _paths
    if _paths.sandbox_enabled():
        ws_abs = os.path.abspath(_get_workspace_root())
        resolved = _paths.resolve_workspace_path(path, ws_abs)
        if not _paths.check_sandbox(resolved, ws_abs):
            raise ValueError(f"沙箱限制: {path} 不在工作目录内")
        return resolved
    if os.path.isabs(path):
        return path
    ws_root = _get_workspace_root()
    ws_abs = os.path.abspath(ws_root)
    candidate = os.path.join(os.getcwd(), path)
    if os.path.exists(candidate):
        return candidate
    ws = os.path.join(ws_abs, path)
    if os.path.exists(ws):
        return ws
    norm = os.path.normpath(path)
    ws_norm = os.path.normpath(ws_root)
    if norm.startswith(ws_norm + os.sep):
        stripped = norm[len(ws_norm + os.sep):]
        ws2 = os.path.join(ws_abs, stripped)
        if os.path.exists(ws2):
            return ws2
    return candidate


def _mgr():
    from entities._sdk import get_llm_manager
    return get_llm_manager()


_MEDIA_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
_media_config_cache: Optional[dict] = None


def _media_config() -> dict:
    """加载媒体工具配置（默认音色、风格预设），进程级缓存。"""
    global _media_config_cache
    if _media_config_cache is None:
        try:
            with open(_MEDIA_CONFIG_FILE, encoding="utf-8") as f:
                _media_config_cache = json.load(f)
        except Exception:
            _media_config_cache = {}
    return _media_config_cache


def _apply_style(prompt: str, style: str) -> str:
    """将风格预设拼接到提示词末尾；未命中预设时按原始风格描述拼接。"""
    if not style.strip():
        return prompt
    presets = _media_config().get("style_presets", {}) or {}
    suffix = presets.get(style.strip(), style.strip())
    return f"{prompt}, {suffix}"


async def _media_with_fallback(
    model_type: str,
    label: str,
    fn: Callable[[str, Any], Awaitable[Any]],
) -> str:
    """按优先级遍历指定类型的模型，依次尝试 fn(model, client)，第一个成功即返回。

    fn 应返回一个可 JSON 序列化的 dict（含 success=True），或抛出异常触发回退。
    """
    pairs = _mgr().iter_media_for_type(model_type)
    if not pairs:
        return json.dumps({"error": f"未配置 {label} 模型（{model_type}类型）"}, ensure_ascii=False)

    last_err = ""
    for model_name, client in pairs:
        try:
            result = await fn(model_name, client)
            if isinstance(result, dict):
                result.setdefault("model", model_name)
                result.setdefault("success", True)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            last_err = f"{model_name}: {exc}"
            from core.log import log
            log(f"{label}模型 {model_name} 调用失败，尝试下一个: {exc}", "WARNING", tag="媒体")
            continue

    return json.dumps({"error": f"所有 {label} 模型均调用失败，最后错误: {last_err}"}, ensure_ascii=False)


# ==================================================================
# 图片识别（vision）— 已有回退机制，保持但统一风格
# ==================================================================

@tool(name="recognize_image", group="media", tags=["media:image", "media:video"], timeout=120.0)
async def recognize_image(image_path: str = "", prompt: str = "", **kwargs: str) -> str:
    """识别/分析图片内容。支持本地文件路径或 URL。

    Args:
        image_path: 图片的绝对路径或 URL
        prompt: 可选的分析提示，如"描述图片中的文字"
    """
    if not image_path:
        image_path = (
            kwargs.get("media_file", "")
            or kwargs.get("image_source", "")
            or kwargs.get("path", "")
            or kwargs.get("file_path", "")
            or kwargs.get("url", "")
        )
    if image_path.startswith("image:"):
        return json.dumps({"error": f"image_path 不需要 'image:' 前缀，请直接传路径: {image_path[6:]}"}, ensure_ascii=False)
    try:
        mgr = _mgr()
        from entities._sdk import load_image_from_path, get_image_content_class, get_model_type_enum
        ImageContent = get_image_content_class()
        ModelType = get_model_type_enum()

        if not image_path:
            return json.dumps({"error": "未提供图片路径或 URL，请使用 image_path 参数"}, ensure_ascii=False)

        is_url = image_path.startswith(("http://", "https://"))
        desc_prompt = prompt or "请简要描述这张图片的内容。"

        all_vision = mgr.get_all_by_type(ModelType.VISION)
        if not all_vision:
            return json.dumps({"error": "未配置视觉模型"}, ensure_ascii=False)

        last_err = ""

        if is_url:
            from entities._sdk import download_image_to_base64
            url_candidates = [c for c in all_vision if c.config.supports_url_vision]
            if url_candidates:
                url_img = ImageContent(data=image_path, is_url=True)
                for vc in url_candidates:
                    try:
                        description = await vc.describe_images([url_img], prompt=desc_prompt)
                        return json.dumps({"success": True, "description": description, "image_path": image_path, "model": vc.config.name}, ensure_ascii=False)
                    except Exception as exc:
                        last_err = str(exc)
                        continue

            b64_img = await download_image_to_base64(image_path)
            if not b64_img:
                return json.dumps({"error": f"无法下载图片: {image_path}"}, ensure_ascii=False)
            b64_candidates = [c for c in all_vision if c.config.supports_base64_vision]
            for vc in (b64_candidates or all_vision):
                try:
                    description = await vc.describe_images([b64_img], prompt=desc_prompt)
                    return json.dumps({"success": True, "description": description, "image_path": image_path, "model": vc.config.name}, ensure_ascii=False)
                except Exception as exc:
                    last_err = str(exc)
                    continue
        else:
            try:
                resolved = _resolve_workspace_path(image_path)
            except ValueError as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)
            if not os.path.exists(resolved):
                return json.dumps({"error": f"文件不存在: {image_path}", "resolved": resolved}, ensure_ascii=False)
            img = load_image_from_path(resolved)
            b64_candidates = [c for c in all_vision if c.config.supports_base64_vision]
            for vc in (b64_candidates or all_vision):
                try:
                    description = await vc.describe_images([img], prompt=desc_prompt)
                    return json.dumps({"success": True, "description": description, "image_path": image_path, "model": vc.config.name}, ensure_ascii=False)
                except Exception as exc:
                    last_err = str(exc)
                    continue

        return json.dumps({"error": f"所有视觉模型均调用失败: {last_err}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 语音识别 ASR — 带回退
# ==================================================================

@tool(name="voice_to_text", group="media", tags=["media:voice", "media:audio"])
async def voice_to_text(audio_source: str = "", **kwargs: str) -> str:
    """将语音/音频文件转写为文字（ASR 语音识别）。支持本地文件路径或 URL。

    Args:
        audio_source: 音频文件的本地路径（如 workspace/uploads/voice/xxx.ogg）或 URL
    """
    if not audio_source:
        audio_source = kwargs.get("path", "") or kwargs.get("file_path", "") or kwargs.get("url", "")
    if not audio_source:
        return json.dumps({"error": "未提供音频路径或 URL"}, ensure_ascii=False)

    is_url = audio_source.startswith(("http://", "https://"))

    if not is_url:
        try:
            resolved = _resolve_workspace_path(audio_source)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        if not os.path.exists(resolved):
            return json.dumps({"error": f"文件不存在: {audio_source}", "resolved": resolved}, ensure_ascii=False)
    else:
        resolved = audio_source

    async def _try(model: str, client: Any) -> dict:
        if is_url:
            text = await client.transcribe_url(resolved, model=model)
        else:
            file_name = os.path.basename(resolved)
            with open(resolved, "rb") as f:
                audio_data = f.read()
            text = await client.transcribe(audio_data, model=model, file_name=file_name)
        return {"text": text}

    return await _media_with_fallback("asr", "语音识别", _try)


# ==================================================================
# 语音合成 TTS — 带回退
# ==================================================================

@tool(name="text_to_voice", group="media")
async def text_to_voice(
    text: str,
    voice: str = "",
    reference_audio: str = "",
    reference_text: str = "",
) -> str:
    """将文字转换为语音音频（TTS 语音合成），返回生成的音频文件路径。

    有两种发声方式（二选一）：
    1. 预置音色：通过 voice 参数选择，可选 alex/anna/bella/benjamin/charles/claire/david/diana
    2. 声音克隆：通过 reference_audio 提供参考音频 + reference_text 对应文字

    两者都不传时，使用 config.json 中配置的默认音色（专属音色优先）。

    Args:
        text: 要转换为语音的文字内容
        voice: 预置音色名称
        reference_audio: 声音克隆的参考音频（URL 或本地路径），与 voice 互斥
        reference_text: 参考音频中的文字内容（克隆时必须提供）
    """
    if not voice and not reference_audio:
        cfg = _media_config()
        default_ref = cfg.get("default_reference_audio", "")
        if default_ref:
            reference_audio = default_ref
            reference_text = reference_text or cfg.get("default_reference_text", "")
        else:
            voice = cfg.get("default_voice", "")

    if reference_audio and not reference_text:
        return json.dumps({"error": "使用声音克隆时必须提供 reference_text"}, ensure_ascii=False)

    references = None
    if reference_audio:
        audio_value = reference_audio
        if not audio_value.startswith(("http://", "https://", "data:audio/")):
            try:
                resolved = _resolve_workspace_path(audio_value)
            except ValueError as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)
            if not os.path.exists(resolved):
                return json.dumps({"error": f"参考音频文件不存在: {audio_value}"}, ensure_ascii=False)
            import base64, mimetypes
            mime_type = mimetypes.guess_type(os.path.basename(resolved))[0] or "audio/mpeg"
            with open(resolved, "rb") as f:
                raw = f.read()
            audio_value = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"
        references = [{"audio": audio_value, "text": reference_text}]

    async def _try(model: str, client: Any) -> dict:
        voice_param = ""
        if not references and voice:
            voice_param = f"{model}:{voice}" if ":" not in voice else voice
        audio_bytes = await client.text_to_speech(
            text, model=model, voice=voice_param, references=references,
        )
        path = client.save_audio_temp(audio_bytes, suffix=".mp3")
        return {"file_path": path, "size_bytes": len(audio_bytes)}

    return await _media_with_fallback("tts", "语音合成", _try)


# ==================================================================
# 视频生成 — 带回退
# ==================================================================

@tool(name="generate_video", group="media")
async def generate_video(prompt: str, image_url: str = "", style: str = "") -> str:
    """根据文字描述生成视频（可选提供参考图片进行图生视频）。

    Args:
        prompt: 视频内容的文字描述
        image_url: 可选的参考图片 URL（用于图生视频）
        style: 可选风格预设名（见 config.json 的 style_presets）或自定义风格描述
    """
    prompt = _apply_style(prompt, style)

    async def _try(model: str, client: Any) -> dict:
        video_url = await client.generate_video(prompt, model=model, image_url=image_url)
        return {"video_url": video_url}

    return await _media_with_fallback("video", "视频生成", _try)


# ==================================================================
# 图片生成 — 带回退
# ==================================================================

@tool(name="generate_image", group="media", tags=["media:image_gen"])
async def generate_image(
    prompt: str,
    image_size: str = "1024x1024",
    num_inference_steps: int = 20,
    style: str = "",
) -> str:
    """根据文字描述生成图片（文生图）。生成结果保存到本地并返回文件路径。

    Args:
        prompt: 图片内容的文字描述
        image_size: 图片尺寸，如 "1024x1024"、"1664x928"(16:9)、"928x1664"(9:16)
        num_inference_steps: 推理步数，默认 20，越高越精细但更慢
        style: 可选风格预设名（见 config.json 的 style_presets，如 nekomimi_maid）
            或自定义风格描述，用于锁定画风
    """
    prompt = _apply_style(prompt, style)

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "image")

    async def _try(model: str, client: Any) -> dict:
        image_results = await client.generate_image(
            prompt, model=model, image_size=image_size,
            num_inference_steps=num_inference_steps,
        )
        if not image_results:
            raise RuntimeError("未返回结果")
        saved_paths = await client.download_and_save_images(image_results, save_dir)
        return {"file_paths": saved_paths, "prompt": prompt}

    return await _media_with_fallback("image_gen", "图片生成", _try)


# ==================================================================
# 图片编辑 — 带回退
# ==================================================================

@tool(name="edit_image", group="media", tags=["media:image_edit"])
async def edit_image(
    image_path: str,
    prompt: str,
    num_inference_steps: int = 20,
) -> str:
    """对已有图片按文字指令进行编辑/修改，返回编辑后图片的文件路径。

    Args:
        image_path: 要编辑的图片，本地路径或 URL
        prompt: 编辑指令，描述希望如何修改图片
        num_inference_steps: 推理步数，默认 20
    """
    if image_path.startswith(("http://", "https://")):
        resolved_image = image_path
    else:
        try:
            resolved_image = _resolve_workspace_path(image_path)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        if not os.path.exists(resolved_image):
            return json.dumps({"error": f"图片不存在: {image_path}", "resolved": resolved_image}, ensure_ascii=False)

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "image")

    async def _try(model: str, client: Any) -> dict:
        image_results = await client.edit_image(
            prompt, model=model, image_path=resolved_image,
            num_inference_steps=num_inference_steps,
        )
        if not image_results:
            raise RuntimeError("未返回结果")
        saved_paths = await client.download_and_save_images(image_results, save_dir)
        return {"file_paths": saved_paths, "prompt": prompt, "source_image": image_path}

    return await _media_with_fallback("image_edit", "图片编辑", _try)


# ==================================================================
# 文档重排序 — 带回退
# ==================================================================

@tool(name="rerank_search", group="media")
async def rerank_search(query: str, documents: str) -> str:
    """按相关性对文档列表重新排序。documents 应为 JSON 字符串数组。

    Args:
        query: 查询语句
        documents: JSON 格式的文档字符串数组，如 '["文档1", "文档2"]'
    """
    try:
        doc_list = json.loads(documents)
        if not isinstance(doc_list, list):
            return json.dumps({"error": "documents 必须是 JSON 字符串数组"}, ensure_ascii=False)
    except json.JSONDecodeError:
        doc_list = [d.strip() for d in documents.split("\n") if d.strip()]

    async def _try(model: str, client: Any) -> dict:
        results = await client.rerank(query, doc_list, model=model)
        return {"query": query, "results": results}

    return await _media_with_fallback("rerank", "文档重排序", _try)
