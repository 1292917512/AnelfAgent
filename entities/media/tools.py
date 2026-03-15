"""多模态媒体工具：图片识别、语音识别、语音合成、图片生成/编辑、视频生成、文档重排序。"""

from __future__ import annotations

import json
import os

from entities._sdk import tool, entity

entity("media", "多模态媒体 - 图片识别、语音转文字、文字转语音、图片生成、图片编辑、视频生成、文档重排序")


def _get_workspace_root() -> str:
    """Get workspace root from ConfigManager or default."""
    try:
        from core.config import ConfigManager
        return ConfigManager.get("workspace_root", "workspace")
    except Exception:
        return "workspace"


def _resolve_workspace_path(path: str) -> str:
    """Resolve a path that may be relative to workspace or CWD."""
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    ws_root = _get_workspace_root()
    ws_abs = os.path.abspath(ws_root)
    # Try as-is from CWD first (handles "workspace/uploads/..." correctly)
    candidate = os.path.join(os.getcwd(), path)
    if os.path.exists(candidate):
        return candidate
    # Try with workspace prefix (handles "uploads/..." when workspace is root)
    ws = os.path.join(ws_abs, path)
    if os.path.exists(ws):
        return ws
    # Strip workspace prefix if doubled
    norm = os.path.normpath(path)
    ws_norm = os.path.normpath(ws_root)
    if norm.startswith(ws_norm + os.sep):
        stripped = norm[len(ws_norm + os.sep):]
        ws2 = os.path.join(ws_abs, stripped)
        if os.path.exists(ws2):
            return ws2
    return candidate


def _get_llm_manager():
    from entities._sdk import get_llm_manager
    return get_llm_manager()


def _get_media_client(model_type: str = ""):
    return _get_llm_manager().get_media_client(model_type)


def _get_image_gen_client():
    return _get_llm_manager().get_image_gen_client()


def _get_image_edit_client():
    return _get_llm_manager().get_image_edit_client()


@tool(name="recognize_image", group="media", tags=["media:image", "media:video"])
async def recognize_image(image_path: str = "", prompt: str = "", **kwargs: str) -> str:
    """识别/分析图片内容。支持本地文件路径或 URL。

    Args:
        image_path: 必须使用此参数传递图片的绝对路径或 URL，直接使用系统提供的完整路径即可，不要加 "image:" 等前缀
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
        mgr = _get_llm_manager()
        from entities._sdk import load_image_from_path, get_image_content_class, get_model_type_enum
        ImageContent = get_image_content_class()
        ModelType = get_model_type_enum()

        if not image_path:
            return json.dumps({"error": "未提供图片路径或 URL，请使用 image_path 参数"}, ensure_ascii=False)

        is_url = image_path.startswith(("http://", "https://"))

        desc_prompt = prompt or "请简要描述这张图片的内容。"

        # 按 vision 优先级列表获取视觉模型（用户可在 web 界面管理顺序）
        all_vision = mgr.get_all_by_type(ModelType.VISION)
        if not all_vision:
            return json.dumps({"error": "未配置视觉模型"}, ensure_ascii=False)

        last_err = ""

        if is_url:
            from entities._sdk import download_image_to_base64
            # 优先尝试：直接传 URL，让支持 URL vision 的模型原生处理（无需下载）
            url_candidates = [c for c in all_vision if c.config.supports_url_vision]
            if url_candidates:
                url_img = ImageContent(data=image_path, is_url=True)
                for vc in url_candidates:
                    try:
                        description = await vc.describe_images([url_img], prompt=desc_prompt)
                        return json.dumps({
                            "success": True,
                            "description": description,
                            "image_path": image_path,
                            "model": vc.config.name,
                        }, ensure_ascii=False)
                    except Exception as exc:
                        last_err = str(exc)
                        continue

            # fallback：下载为 base64，使用支持 base64 的模型
            b64_img = await download_image_to_base64(image_path)
            if not b64_img:
                return json.dumps({"error": f"无法下载图片: {image_path}"}, ensure_ascii=False)
            b64_candidates = [c for c in all_vision if c.config.supports_base64_vision]
            candidates = b64_candidates or all_vision
            for vc in candidates:
                try:
                    description = await vc.describe_images([b64_img], prompt=desc_prompt)
                    return json.dumps({
                        "success": True,
                        "description": description,
                        "image_path": image_path,
                        "model": vc.config.name,
                    }, ensure_ascii=False)
                except Exception as exc:
                    last_err = str(exc)
                    continue
        else:
            resolved = _resolve_workspace_path(image_path)
            if not os.path.exists(resolved):
                return json.dumps({"error": f"文件不存在: {image_path}", "resolved": resolved}, ensure_ascii=False)
            img = load_image_from_path(resolved)
            b64_candidates = [c for c in all_vision if c.config.supports_base64_vision]
            candidates = b64_candidates or all_vision
            for vc in candidates:
                try:
                    description = await vc.describe_images([img], prompt=desc_prompt)
                    return json.dumps({
                        "success": True,
                        "description": description,
                        "image_path": image_path,
                        "model": vc.config.name,
                    }, ensure_ascii=False)
                except Exception as exc:
                    last_err = str(exc)
                    continue

        return json.dumps({"error": f"视觉模型调用失败: {last_err}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="voice_to_text", group="media", tags=["media:voice", "media:audio"])
async def voice_to_text(audio_source: str = "", **kwargs: str) -> str:
    """将语音/音频文件转写为文字（ASR 语音识别）。支持本地文件路径或 URL。

    Args:
        audio_source: 音频文件的本地路径（如 workspace/uploads/voice/xxx.ogg）或 URL
    """
    if not audio_source:
        audio_source = kwargs.get("path", "") or kwargs.get("file_path", "") or kwargs.get("url", "")
    try:
        mgr = _get_llm_manager()
        model = mgr.get_asr_model()
        if not model:
            return json.dumps({"error": "未配置语音识别（ASR）模型，无法使用语音转文字功能"}, ensure_ascii=False)

        client = _get_media_client("asr")
        if not client:
            return json.dumps({"error": "未配置媒体客户端（需要 asr 类型的模型）"}, ensure_ascii=False)

        if audio_source.startswith(("http://", "https://")):
            text = await client.transcribe_url(audio_source, model=model)
        else:
            resolved = _resolve_workspace_path(audio_source)
            if not os.path.exists(resolved):
                return json.dumps({"error": f"文件不存在: {audio_source}", "resolved": resolved}, ensure_ascii=False)
            file_name = os.path.basename(resolved)
            with open(resolved, "rb") as f:
                audio_data = f.read()
            text = await client.transcribe(audio_data, model=model, file_name=file_name)

        return json.dumps({"success": True, "text": text, "model": model}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="text_to_voice", group="media")
async def text_to_voice(
    text: str,
    voice: str = "",
    reference_audio: str = "",
    reference_text: str = "",
) -> str:
    """将文字转换为语音音频（TTS 语音合成），返回生成的音频文件路径。

    有两种发声方式（二选一）：
    1. **预置音色**：通过 voice 参数选择内置音色，可选值：
       alex / anna / bella / benjamin / charles / claire / david / diana。
       不传则默认使用 alex。
    2. **声音克隆**：通过 reference_audio 提供一段参考音频（URL 或本地路径），
       同时用 reference_text 说明该音频对应的文字内容，系统将克隆该声音来朗读 text。
       使用克隆时 voice 参数会被忽略。

    Args:
        text: 要转换为语音的文字内容
        voice: 预置音色名称，可选 alex/anna/bella/benjamin/charles/claire/david/diana
        reference_audio: 声音克隆的参考音频，可以是 URL 或本地文件路径（与 voice 互斥）
        reference_text: 参考音频中说的文字内容（使用声音克隆时必须提供）
    """
    try:
        mgr = _get_llm_manager()
        model = mgr.get_tts_model()
        if not model:
            return json.dumps({"error": "未配置语音合成（TTS）模型，无法使用文字转语音功能"}, ensure_ascii=False)

        client = _get_media_client("tts")
        if not client:
            return json.dumps({"error": "未配置媒体客户端（需要 tts 类型的模型）"}, ensure_ascii=False)

        references = None
        if reference_audio:
            if not reference_text:
                return json.dumps({"error": "使用声音克隆时必须提供 reference_text（参考音频对应的文字）"}, ensure_ascii=False)
            audio_value = reference_audio
            if not audio_value.startswith(("http://", "https://", "data:audio/")):
                resolved = _resolve_workspace_path(audio_value)
                if not os.path.exists(resolved):
                    return json.dumps({"error": f"参考音频文件不存在: {audio_value}", "resolved": resolved}, ensure_ascii=False)
                import base64
                import mimetypes
                mime_type, _ = mimetypes.guess_type(os.path.basename(resolved))
                mime_type = mime_type or "audio/mpeg"
                with open(resolved, "rb") as f:
                    raw = f.read()
                audio_value = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"
            references = [{"audio": audio_value, "text": reference_text}]

        voice_param = ""
        if not references and voice:
            voice_param = f"{model}:{voice}" if ":" not in voice else voice

        audio_bytes = await client.text_to_speech(
            text, model=model, voice=voice_param, references=references,
        )
        path = client.save_audio_temp(audio_bytes, suffix=".mp3")
        return json.dumps({
            "success": True,
            "file_path": path,
            "size_bytes": len(audio_bytes),
            "model": model,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="generate_video", group="media")
async def generate_video(prompt: str, image_url: str = "") -> str:
    """根据文字描述生成视频（可选提供参考图片进行图生视频）。

    Args:
        prompt: 视频内容的文字描述
        image_url: 可选的参考图片 URL（用于图生视频）
    """
    try:
        mgr = _get_llm_manager()
        model = mgr.get_video_model()
        if not model:
            return json.dumps({"error": "未配置视频生成模型，无法使用视频生成功能"}, ensure_ascii=False)

        client = _get_media_client("video")
        if not client:
            return json.dumps({"error": "未配置媒体客户端（需要 video 类型的模型）"}, ensure_ascii=False)

        video_url = await client.generate_video(prompt, model=model, image_url=image_url)
        return json.dumps({
            "success": True,
            "video_url": video_url,
            "model": model,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="generate_image", group="media", tags=["media:image_gen"])
async def generate_image(
    prompt: str,
    image_size: str = "1024x1024",
    num_inference_steps: int = 20,
) -> str:
    """根据文字描述生成图片（文生图）。生成结果保存到本地并返回文件路径。

    Args:
        prompt: 图片内容的文字描述
        image_size: 图片尺寸。Qwen-Image 推荐值："1328x1328"(1:1)、"1664x928"(16:9)、"928x1664"(9:16) 等
        num_inference_steps: 推理步数，默认 20，越高越精细但更慢
    """
    try:
        mgr = _get_llm_manager()
        model = mgr.get_image_gen_model()
        if not model:
            return json.dumps({"error": "未配置图片生成（image_gen）模型，无法使用图片生成功能"}, ensure_ascii=False)

        client = _get_image_gen_client()
        if not client:
            return json.dumps({"error": "未配置图片生成客户端（需要 image_gen 类型的模型）"}, ensure_ascii=False)

        image_results = await client.generate_image(
            prompt, model=model, image_size=image_size,
            num_inference_steps=num_inference_steps,
        )
        if not image_results:
            return json.dumps({"error": "图片生成失败，未返回结果"}, ensure_ascii=False)

        ws_root = _get_workspace_root()
        save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "image")
        saved_paths = await client.download_and_save_images(image_results, save_dir)

        return json.dumps({
            "success": True,
            "file_paths": saved_paths,
            "model": model,
            "prompt": prompt,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="edit_image", group="media", tags=["media:image_edit"])
async def edit_image(
    image_path: str,
    prompt: str,
    num_inference_steps: int = 20,
) -> str:
    """对已有图片按文字指令进行编辑/修改，返回编辑后图片的文件路径。

    Args:
        image_path: 要编辑的图片，本地路径（如 workspace/uploads/image/xxx.jpg）或 URL
        prompt: 编辑指令，描述希望如何修改图片
        num_inference_steps: 推理步数，默认 20
    """
    try:
        mgr = _get_llm_manager()
        model = mgr.get_image_edit_model()
        if not model:
            return json.dumps({"error": "未配置图片编辑（image_edit）模型，无法使用图片编辑功能"}, ensure_ascii=False)

        client = _get_image_edit_client()
        if not client:
            return json.dumps({"error": "未配置图片编辑客户端（需要 image_edit 类型的模型）"}, ensure_ascii=False)

        if image_path.startswith(("http://", "https://")):
            resolved_image = image_path
        else:
            resolved_image = _resolve_workspace_path(image_path)
            if not os.path.exists(resolved_image):
                return json.dumps({"error": f"图片不存在: {image_path}", "resolved": resolved_image}, ensure_ascii=False)

        image_results = await client.edit_image(
            prompt, model=model, image_path=resolved_image,
            num_inference_steps=num_inference_steps,
        )
        if not image_results:
            return json.dumps({"error": "图片编辑失败，未返回结果"}, ensure_ascii=False)

        ws_root = _get_workspace_root()
        save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "image")
        saved_paths = await client.download_and_save_images(image_results, save_dir)

        return json.dumps({
            "success": True,
            "file_paths": saved_paths,
            "model": model,
            "prompt": prompt,
            "source_image": image_path,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="rerank_search", group="media")
async def rerank_search(query: str, documents: str) -> str:
    """按相关性对文档列表重新排序。documents 应为 JSON 字符串数组。

    Args:
        query: 查询语句
        documents: JSON 格式的文档字符串数组，如 '["文档1", "文档2"]'
    """
    try:
        mgr = _get_llm_manager()
        model = mgr.get_rerank_model()
        if not model:
            return json.dumps({"error": "未配置重排序模型，无法使用文档重排序功能"}, ensure_ascii=False)

        client = _get_media_client("rerank")
        if not client:
            return json.dumps({"error": "未配置媒体客户端（需要 rerank 类型的模型）"}, ensure_ascii=False)

        try:
            doc_list = json.loads(documents)
            if not isinstance(doc_list, list):
                return json.dumps({"error": "documents 必须是 JSON 字符串数组"}, ensure_ascii=False)
        except json.JSONDecodeError:
            doc_list = [d.strip() for d in documents.split("\n") if d.strip()]

        results = await client.rerank(query, doc_list, model=model)
        return json.dumps({
            "success": True,
            "query": query,
            "results": results,
            "model": model,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
