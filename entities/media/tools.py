"""多模态媒体工具：图片识别、语音识别、语音合成、图片生成/编辑、视频生成、文档重排序。"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from entities._sdk import ErrorCause, entity, error_from_exception, tool, tool_error

entity("media", "多模态媒体 - 图片识别、语音转文字、文字转语音、音色管理、音乐生成、图片生成、图片编辑、视频生成、文档重排序")


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


def _classify_media_errors(errors: Dict[str, str]) -> Tuple[ErrorCause, bool, str]:
    """根据各模型错误详情推断整体归因，让 AI 拿到可决策的 cause/hint。"""
    detail = " ".join(errors.values()).lower()
    if any(k in detail for k in ("http 401", "http 403", "(1004)", "(2049)", "invalid api key", "unauthorized")):
        return (ErrorCause.CONFIG, False, "API Key 无效或无权限，请检查模型供应商的密钥配置")
    if any(k in detail for k in ("http 402", "(1008)", "余额", "insufficient")):
        return (ErrorCause.CONFIG, False, "账户余额不足，请充值后重试")
    if any(k in detail for k in ("http 422", "(1026)", "(1027)", "敏感")):
        return (ErrorCause.PARAM, False, "内容触发平台敏感审核，请调整提示词/素材后重试")
    if "timeout" in detail or "超时" in detail:
        return (ErrorCause.TIMEOUT, True, "可稍后重试")
    return (ErrorCause.NETWORK, True, "可稍后重试，或在模型配置中调整该类型模型的优先级/更换模型")


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
        return tool_error(f"未配置 {label} 模型（{model_type}类型）",
                          cause=ErrorCause.CONFIG, retryable=False,
                          hint="请先在模型配置中添加对应类型的模型")

    errors: Dict[str, str] = {}
    for model_name, client in pairs:
        try:
            result = await fn(model_name, client)
            if isinstance(result, dict):
                result.setdefault("model", model_name)
                result.setdefault("success", True)
            return json.dumps(result, ensure_ascii=False)
        except NotImplementedError:
            # 协议本身不支持该操作（非单个模型故障），直接向上抛出
            raise
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            errors[model_name] = detail[:200]
            from core.log import log
            log(f"{label}模型 {model_name} 调用失败，尝试下一个: {detail}", "WARNING", tag="媒体")
            continue

    cause, retryable, hint = _classify_media_errors(errors)
    summary = "; ".join(f"{m}: {e}" for m, e in errors.items())
    return tool_error(f"所有 {label} 模型均调用失败（{summary}）",
                      cause=cause, retryable=retryable, hint=hint,
                      errors=errors)


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
        return tool_error(f"image_path 不需要 'image:' 前缀，请直接传路径: {image_path[6:]}",
                          cause=ErrorCause.PARAM, retryable=False)
    try:
        mgr = _mgr()
        from entities._sdk import get_image_content_class, get_model_type_enum, load_image_from_path
        ImageContent = get_image_content_class()
        ModelType = get_model_type_enum()

        if not image_path:
            return tool_error("未提供图片路径或 URL，请使用 image_path 参数",
                              cause=ErrorCause.PARAM, retryable=False)

        is_url = image_path.startswith(("http://", "https://"))
        desc_prompt = prompt or "请简要描述这张图片的内容。"

        all_vision = mgr.get_all_by_type(ModelType.VISION)
        if not all_vision:
            return tool_error("未配置视觉模型", cause=ErrorCause.CONFIG, retryable=False,
                              hint="请先在模型配置中添加视觉（vision）模型")

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
                return tool_error(f"无法下载图片: {image_path}",
                                  cause=ErrorCause.NETWORK, retryable=True,
                                  hint="检查图片 URL 是否可访问后重试，或改用本地图片路径")
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
                return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                                  hint="请使用工作目录（workspace）内的路径")
            if not os.path.exists(resolved):
                return tool_error(f"文件不存在: {image_path}", cause=ErrorCause.NOT_FOUND,
                                  retryable=False, resolved=resolved)
            img = load_image_from_path(resolved)
            b64_candidates = [c for c in all_vision if c.config.supports_base64_vision]
            for vc in (b64_candidates or all_vision):
                try:
                    description = await vc.describe_images([img], prompt=desc_prompt)
                    return json.dumps({"success": True, "description": description, "image_path": image_path, "model": vc.config.name}, ensure_ascii=False)
                except Exception as exc:
                    last_err = str(exc)
                    continue

        return tool_error(f"所有视觉模型均调用失败: {last_err}", retryable=True)
    except Exception as e:
        return error_from_exception(e, action="识别图片")


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
        return tool_error("未提供音频路径或 URL", cause=ErrorCause.PARAM, retryable=False)

    is_url = audio_source.startswith(("http://", "https://"))

    if not is_url:
        try:
            resolved = _resolve_workspace_path(audio_source)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved):
            return tool_error(f"文件不存在: {audio_source}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=resolved)
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

@tool(name="text_to_voice", group="media", timeout=300.0)
async def text_to_voice(
    text: str,
    voice: str = "",
    reference_audio: str = "",
    reference_text: str = "",
    emotion: str = "",
    speed: float = 0.0,
) -> str:
    """将文字转换为语音音频（TTS 语音合成），保存到本地并返回文件路径。

    发声方式（二选一）：
    1. 预置音色：voice 参数（MiniMax 如 male-qn-qingse/female-yujie，可用 list_voices 查询；
       SiliconFlow 如 alex/anna/bella 等）
    2. 声音克隆：reference_audio 参考音频 + reference_text 对应文字（仅 SiliconFlow）

    两者都不传时，使用 config.json 中配置的默认音色。超过 3000 字的长文本
    在支持的协议上自动走异步合成。

    Args:
        text: 要转换为语音的文字内容
        voice: 预置音色 ID
        reference_audio: 声音克隆的参考音频（URL 或本地路径），与 voice 互斥
        reference_text: 参考音频中的文字内容（克隆时必须提供）
        emotion: 情绪（仅 MiniMax）：happy/sad/angry/fearful/disgusted/surprised/calm/fluent
        speed: 语速 0.5~2.0，0 表示默认 1.0（仅 MiniMax）
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
        return tool_error("使用声音克隆时必须提供 reference_text",
                          cause=ErrorCause.PARAM, retryable=False)

    references = None
    if reference_audio:
        audio_value = reference_audio
        if not audio_value.startswith(("http://", "https://", "data:audio/")):
            try:
                resolved = _resolve_workspace_path(audio_value)
            except ValueError as e:
                return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                                  hint="请使用工作目录（workspace）内的路径")
            if not os.path.exists(resolved):
                return tool_error(f"参考音频文件不存在: {audio_value}",
                                  cause=ErrorCause.NOT_FOUND, retryable=False)
            import base64
            import mimetypes
            mime_type = mimetypes.guess_type(os.path.basename(resolved))[0] or "audio/mpeg"
            with open(resolved, "rb") as f:
                raw = f.read()
            audio_value = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"
        references = [{"audio": audio_value, "text": reference_text}]

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "audio")

    async def _try(model: str, client: Any) -> dict:
        audio_bytes = await client.text_to_speech(
            text, model=model, voice=voice, references=references,
            emotion=emotion, speed=speed or None,
        )
        path = client.save_audio_file(audio_bytes, save_dir, suffix=".mp3")
        return {"file_path": path, "size_bytes": len(audio_bytes)}

    return await _media_with_fallback("tts", "语音合成", _try)


# ==================================================================
# 音色管理（仅 MiniMax 协议支持）
# ==================================================================

@tool(name="clone_voice", group="media", timeout=300.0)
async def clone_voice(
    audio_path: str,
    voice_id: str,
    preview_text: str = "",
) -> str:
    """音色复刻：用一段音频克隆声音，之后可在 text_to_voice 的 voice 参数中使用该 voice_id。

    Args:
        audio_path: 克隆源音频（本地路径，mp3/m4a/wav，10 秒~5 分钟，≤20MB）
        voice_id: 自定义音色 ID（8-256 字符，字母开头，可含数字/横线/下划线）
        preview_text: 可选试听文本（克隆后用新音色朗读，≤1000 字）
    """
    if not voice_id.strip():
        return tool_error("未提供 voice_id", cause=ErrorCause.PARAM, retryable=False)
    try:
        resolved = _resolve_workspace_path(audio_path)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                          hint="请使用工作目录（workspace）内的路径")
    if not os.path.exists(resolved):
        return tool_error(f"音频文件不存在: {audio_path}", cause=ErrorCause.NOT_FOUND,
                          retryable=False)

    async def _try(model: str, client: Any) -> dict:
        result = await client.voice_clone(
            resolved, voice_id=voice_id.strip(), preview_text=preview_text, model=model,
        )
        return {"voice_id": voice_id.strip(), **result}

    try:
        return await _media_with_fallback("tts", "音色复刻", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="音色复刻仅 MiniMax 语音协议支持")


@tool(name="design_voice", group="media", timeout=300.0)
async def design_voice(prompt: str, preview_text: str, voice_id: str = "") -> str:
    """音色设计：按文字描述生成新音色，返回 voice_id 与试听音频文件。

    Args:
        prompt: 音色描述（如"悬疑小说旁白，低沉磁性的男声"）
        preview_text: 试听文本（≤500 字，用新音色朗读）
        voice_id: 可选自定义音色 ID，留空自动生成
    """
    if not prompt.strip() or not preview_text.strip():
        return tool_error("prompt 与 preview_text 均不能为空",
                          cause=ErrorCause.PARAM, retryable=False)

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "audio")

    async def _try(model: str, client: Any) -> dict:
        result = await client.voice_design(
            prompt=prompt, preview_text=preview_text, voice_id=voice_id.strip(),
        )
        out: dict = {"voice_id": result.get("voice_id", "")}
        trial = result.get("trial_audio")
        if trial:
            out["preview_file_path"] = client.save_audio_file(trial, save_dir, suffix=".mp3")
        return out

    try:
        return await _media_with_fallback("tts", "音色设计", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="音色设计仅 MiniMax 语音协议支持")


@tool(name="list_voices", group="media")
async def list_voices(voice_type: str = "all") -> str:
    """查询可用音色列表（系统音色/复刻音色/设计音色）。

    Args:
        voice_type: system / voice_cloning / voice_generation / all
    """

    async def _try(model: str, client: Any) -> dict:
        return await client.list_voices(voice_type.strip() or "all")

    try:
        return await _media_with_fallback("tts", "音色查询", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="音色管理仅 MiniMax 语音协议支持")


@tool(name="delete_voice", group="media")
async def delete_voice(voice_id: str, voice_type: str = "voice_cloning") -> str:
    """删除复刻/设计的音色（不可恢复）。

    Args:
        voice_id: 要删除的音色 ID
        voice_type: voice_cloning（复刻）或 voice_generation（设计）
    """
    if not voice_id.strip():
        return tool_error("未提供 voice_id", cause=ErrorCause.PARAM, retryable=False)

    async def _try(model: str, client: Any) -> dict:
        return await client.delete_voice(voice_id.strip(), voice_type.strip() or "voice_cloning")

    try:
        return await _media_with_fallback("tts", "音色删除", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="音色管理仅 MiniMax 语音协议支持")


# ==================================================================
# 音乐生成（仅 MiniMax 协议支持）
# ==================================================================

@tool(name="generate_music", group="media", timeout=300.0)
async def generate_music(
    prompt: str = "",
    lyrics: str = "",
    is_instrumental: bool = False,
) -> str:
    """音乐/歌曲生成，结果保存到本地并返回文件路径。

    三种模式：
    - 歌曲：lyrics 必填（可用 generate_lyrics 先生成歌词），prompt 描述风格
    - 纯音乐：is_instrumental=true，prompt 必填
    - 翻唱：需先经 music_cover 流程（当前通过 prompt + 参考音频由平台处理）

    Args:
        prompt: 音乐风格/情绪描述（≤2000 字）
        lyrics: 歌词（\\n 换行，支持 [Verse]/[Chorus] 等结构标签，≤3500 字）
        is_instrumental: 是否纯音乐（默认否）
    """
    if not prompt.strip() and not lyrics.strip():
        return tool_error("prompt 与 lyrics 至少提供一项",
                          cause=ErrorCause.PARAM, retryable=False)

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "music")

    async def _try(model: str, client: Any) -> dict:
        result = await client.generate_music(
            model=model, prompt=prompt, lyrics=lyrics,
            is_instrumental=bool(is_instrumental),
        )
        path = client.save_audio_file(result.audio, save_dir, suffix=".mp3")
        return {"file_path": path, "extra_info": result.extra_info}

    try:
        return await _media_with_fallback("music", "音乐生成", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="音乐生成仅 MiniMax 协议支持，请在模型配置中添加 music 类型的 MiniMax 模型")


@tool(name="generate_lyrics", group="media")
async def generate_lyrics(
    prompt: str = "",
    lyrics: str = "",
    title: str = "",
    mode: str = "write_full_song",
) -> str:
    """歌词生成：写整首歌词或修改已有歌词（结果可直接用于 generate_music）。

    Args:
        prompt: 歌曲主题/风格描述（≤2000 字）
        lyrics: 已有歌词（mode=edit 时必填，≤3500 字）
        title: 保留的歌名（可选）
        mode: write_full_song（写整首）或 edit（修改已有歌词）
    """

    async def _try(model: str, client: Any) -> dict:
        return await client.generate_lyrics(
            mode=mode.strip() or "write_full_song",
            prompt=prompt, lyrics=lyrics, title=title,
        )

    try:
        return await _media_with_fallback("music", "歌词生成", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="歌词生成仅 MiniMax 协议支持，请在模型配置中添加 music 类型的 MiniMax 模型")


# ==================================================================
# 视频生成 — 带回退
# ==================================================================

def _to_image_value(path_or_url: str) -> str:
    """将图片输入规范化为 URL 或 data:base64；本地路径做沙箱校验并读文件转码。"""
    if path_or_url.startswith(("http://", "https://", "data:image/", "mm_file://")):
        return path_or_url
    resolved = _resolve_workspace_path(path_or_url)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"图片不存在: {path_or_url}")
    import base64
    import mimetypes
    mime_type = mimetypes.guess_type(os.path.basename(resolved))[0] or "image/png"
    with open(resolved, "rb") as f:
        raw = f.read()
    return f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"


def _parse_subject_reference(value: str) -> List[str]:
    """解析主体参考图片参数：支持单个路径/URL 或 JSON 字符串数组。"""
    value = value.strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            items = json.loads(value)
            if isinstance(items, list):
                return [str(item) for item in items if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [value]


@tool(name="generate_video", group="media", timeout=620.0)
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
) -> str:
    """根据文字描述生成视频，结果下载到本地并返回文件路径。

    支持文生视频、图生视频（首帧/尾帧）、主体参考视频，具体能力取决于
    当前视频模型协议（MiniMax 支持全部参数，其他协议仅 prompt + 首帧图）。

    Args:
        prompt: 视频内容的文字描述
        image_url: 首帧参考图（兼容参数，等同 first_frame_image），本地路径或 URL
        first_frame_image: 首帧图片，本地路径或 URL（图生视频）
        last_frame_image: 尾帧图片，本地路径或 URL（首尾帧视频，MiniMax）
        subject_reference: 主体参考图片（MiniMax），单个路径/URL 或 JSON 数组字符串
        duration: 视频时长（秒），0 表示由模型默认（MiniMax-H3: 4-15，Hailuo: 6 或 10）
        resolution: 分辨率，如 "2K"（MiniMax-H3）或 "768P"/"1080P"（Hailuo）
        ratio: 画面比例，如 "16:9"/"9:16"（仅 MiniMax-H3 文生视频有效）
        style: 可选风格预设名（见 config.json 的 style_presets）或自定义风格描述
    """
    prompt = _apply_style(prompt, style)

    try:
        first_frame = _to_image_value(first_frame_image or image_url) if (first_frame_image or image_url) else ""
        last_frame = _to_image_value(last_frame_image) if last_frame_image else ""
        subjects = [_to_image_value(item) for item in _parse_subject_reference(subject_reference)]
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                          hint="请使用工作目录（workspace）内的路径")
    except FileNotFoundError as e:
        return tool_error(str(e), cause=ErrorCause.NOT_FOUND, retryable=False)

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "video")

    async def _try(model: str, client: Any) -> dict:
        video_url = await client.generate_video(
            prompt,
            model=model,
            first_frame_image=first_frame,
            last_frame_image=last_frame,
            subject_reference=subjects,
            duration=duration or None,
            resolution=resolution,
            ratio=ratio,
        )
        if not video_url:
            raise RuntimeError("未返回视频地址")
        file_path = await client.download_and_save_video(video_url, save_dir)
        return {"file_path": file_path, "video_url": video_url, "prompt": prompt}

    return await _media_with_fallback("video", "视频生成", _try)


# ==================================================================
# 视频任务管理（仅 MiniMax 协议支持）
# ==================================================================

@tool(name="query_video_task", group="media", timeout=300.0)
async def query_video_task(task_id: str, download: bool = True) -> str:
    """查询视频生成任务状态；任务成功时可下载视频到本地并返回文件路径。

    Args:
        task_id: 视频任务 ID（MiniMax 平台任务，由创建任务响应或任务列表获得）
        download: 任务成功时是否下载视频到本地（默认是）
    """
    if not task_id.strip():
        return tool_error("未提供 task_id", cause=ErrorCause.PARAM, retryable=False)
    from entities._sdk import coerce_bool_arg
    download = coerce_bool_arg(download, True)

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "video")

    async def _try(model: str, client: Any) -> dict:
        result = await client.query_video_task(task_id.strip(), model=model)
        if result.get("status") == "succeeded" and download and result.get("video_url"):
            result["file_path"] = await client.download_and_save_video(result["video_url"], save_dir)
        return result

    try:
        return await _media_with_fallback("video", "视频任务查询", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="任务管理仅 MiniMax 视频协议支持")


@tool(name="list_video_tasks", group="media")
async def list_video_tasks(page_num: int = 1, page_size: int = 20, status: str = "") -> str:
    """分页查询近 7 天的视频生成任务列表（仅 MiniMax-H3 协议支持）。

    Args:
        page_num: 页码，从 1 开始
        page_size: 每页条数
        status: 可选状态过滤：queued/running/succeeded/failed/cancelled/expired
    """

    async def _try(model: str, client: Any) -> dict:
        return await client.list_video_tasks(
            model=model,
            page_num=max(1, int(page_num)),
            page_size=max(1, int(page_size)),
            status=status.strip(),
        )

    try:
        return await _media_with_fallback("video", "视频任务列表", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="任务管理仅 MiniMax 视频协议支持")


@tool(name="cancel_video_task", group="media")
async def cancel_video_task(task_id: str) -> str:
    """取消排队中的视频任务（不计费）或删除已终结的任务记录（仅 MiniMax-H3 协议支持）。

    Args:
        task_id: 视频任务 ID
    """
    if not task_id.strip():
        return tool_error("未提供 task_id", cause=ErrorCause.PARAM, retryable=False)

    async def _try(model: str, client: Any) -> dict:
        return await client.cancel_or_delete_video_task(task_id.strip(), model=model)

    try:
        return await _media_with_fallback("video", "视频任务取消", _try)
    except NotImplementedError as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="任务管理仅 MiniMax 视频协议支持")


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
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved_image):
            return tool_error(f"图片不存在: {image_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=resolved_image)

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
            return tool_error("documents 必须是 JSON 字符串数组",
                              cause=ErrorCause.PARAM, retryable=False,
                              hint="请传入形如 '[\"文档1\", \"文档2\"]' 的 JSON 字符串")
    except json.JSONDecodeError:
        doc_list = [d.strip() for d in documents.split("\n") if d.strip()]

    async def _try(model: str, client: Any) -> dict:
        results = await client.rerank(query, doc_list, model=model)
        return {"query": query, "results": results}

    return await _media_with_fallback("rerank", "文档重排序", _try)
