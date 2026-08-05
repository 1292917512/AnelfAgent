"""多模态媒体库 — 统一工具面。

架构：tools.py（统一接口/参数归一/沙箱校验/产物落盘）
  → providers.run_capability（按媒体库配置的 provider 优先级链路由）
  → providers/models.py（llm_clients.json 已配置模型）/ providers/minimax.py（MiniMax 直连模块）

provider 参数：auto（默认，按配置优先级链自动路由+失败降级）或指定 provider 名
（models/minimax），可在媒体库配置面板调整各能力优先级。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from typing import Any, Dict, List, Optional

from entities._sdk import ErrorCause, error_from_exception, tool, tool_error

from . import utils
from .config import apply_style, get_default, load_config
from .providers import PROVIDER_NAMES, run_capability


def _check_provider(provider: str) -> Optional[str]:
    """校验 provider 参数合法性，非法返回错误 JSON。"""
    if provider and provider != "auto" and provider not in PROVIDER_NAMES:
        return tool_error(
            f"未知 provider: {provider}",
            cause=ErrorCause.PARAM, retryable=False,
            hint=f"可选: auto / {' / '.join(PROVIDER_NAMES)}",
        )
    return None


def _dumps(out: Dict[str, Any]) -> str:
    return json.dumps(out, ensure_ascii=False)


# ==================================================================
# 图片识别（vision）
# ==================================================================

@tool(name="recognize_image", group="media", tags=["media:image", "media:video"], timeout=120.0)
async def recognize_image(image_path: str = "", prompt: str = "", provider: str = "auto", **kwargs: str) -> str:
    """识别/分析图片或视频内容。支持本地文件路径或 URL。

    Args:
        image_path: 图片/视频的绝对路径或 URL
        prompt: 可选的分析提示，如"描述图片中的文字"
        provider: auto（默认，视觉模型链失败自动降级 MiniMax Coding Plan 订阅配额）/
            models / minimax（直连 Coding Plan，不占视觉模型调用）
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
    if not image_path:
        return tool_error("未提供图片路径或 URL，请使用 image_path 参数",
                          cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err

    from entities._sdk import is_video_path
    is_video = is_video_path(image_path)
    if not image_path.startswith(("http://", "https://", "data:image/")):
        try:
            resolved = utils.resolve_workspace_path(image_path)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved):
            return tool_error(f"文件不存在: {image_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=resolved)
        image_path = resolved

    default_prompt = "请简要描述这个视频的内容。" if is_video else "请简要描述这张图片的内容。"
    desc_prompt = prompt or default_prompt
    try:
        out = await run_capability(
            "vision", "视频识别" if is_video else "图片识别", provider=provider,
            image_path=image_path, prompt=desc_prompt,
        )
        if out.get("success"):
            out["image_path"] = image_path
        return _dumps(out)
    except Exception as e:
        return error_from_exception(e, action="识别图片")


# ==================================================================
# 语音识别 ASR
# ==================================================================

@tool(name="voice_to_text", group="media", tags=["media:voice", "media:audio"])
async def voice_to_text(audio_source: str = "", provider: str = "auto", **kwargs: str) -> str:
    """将语音/音频文件转写为文字（ASR 语音识别）。支持本地文件路径或 URL。

    Args:
        audio_source: 音频文件的本地路径（如 workspace/uploads/voice/xxx.ogg）或 URL
        provider: auto（默认）/ models / minimax
    """
    if not audio_source:
        audio_source = kwargs.get("path", "") or kwargs.get("file_path", "") or kwargs.get("url", "")
    if not audio_source:
        return tool_error("未提供音频路径或 URL", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err

    is_url = audio_source.startswith(("http://", "https://"))
    if not is_url:
        try:
            resolved = utils.resolve_workspace_path(audio_source)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved):
            return tool_error(f"文件不存在: {audio_source}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=resolved)
    else:
        resolved = audio_source

    return _dumps(await run_capability(
        "asr", "语音识别", provider=provider, resolved=resolved, is_url=is_url,
    ))


# ==================================================================
# 语音合成 TTS
# ==================================================================

@tool(name="text_to_voice", group="media", timeout=300.0)
async def text_to_voice(
    text: str,
    voice: str = "",
    reference_audio: str = "",
    reference_text: str = "",
    emotion: str = "",
    speed: float = 0.0,
    pitch: int = 0,
    language_boost: str = "",
    provider: str = "auto",
) -> str:
    """将文字转换为语音音频（TTS 语音合成），保存到本地并返回文件路径。

    发声方式（二选一）：
    1. 预置音色：voice 参数（MiniMax 如 male-qn-qingse/female-yujie，可用 list_voices 查询；
       SiliconFlow 如 alex/anna/bella 等）
    2. 声音克隆：reference_audio 参考音频 + reference_text 对应文字（仅 models 链 OpenAI 风格协议）

    两者都不传时，使用媒体库配置中的默认音色。超过 3000 字的长文本
    在支持的协议上自动走异步合成。

    Args:
        text: 要转换为语音的文字内容
        voice: 预置音色 ID
        reference_audio: 声音克隆的参考音频（URL 或本地路径），与 voice 互斥
        reference_text: 参考音频中的文字内容（克隆时必须提供）
        emotion: 情绪（仅 MiniMax 协议）：happy/sad/angry/fearful/disgusted/surprised/calm/fluent
        speed: 语速 0.5~2.0，0 表示默认（仅 MiniMax 协议）
        pitch: 语调 -12~12，0 表示原音色（仅 MiniMax 协议）
        language_boost: 语种增强（仅 MiniMax 协议）：Chinese/English/Japanese/auto 等
        provider: auto（默认）/ models / minimax
    """
    err = _check_provider(provider)
    if err:
        return err
    if not voice and not reference_audio:
        cfg = load_config()
        default_ref = cfg.get("default_reference_audio", "")
        if default_ref:
            reference_audio = default_ref
            reference_text = reference_text or cfg.get("default_reference_text", "")
        else:
            voice = cfg.get("default_voice", "")

    if reference_audio and not reference_text:
        return tool_error("使用声音克隆时必须提供 reference_text",
                          cause=ErrorCause.PARAM, retryable=False)

    references: Optional[List[Dict[str, str]]] = None
    if reference_audio:
        audio_value = reference_audio
        if not audio_value.startswith(("http://", "https://", "data:audio/")):
            try:
                resolved = utils.resolve_workspace_path(audio_value)
            except ValueError as e:
                return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                                  hint="请使用工作目录（workspace）内的路径")
            if not os.path.exists(resolved):
                return tool_error(f"参考音频文件不存在: {audio_value}",
                                  cause=ErrorCause.NOT_FOUND, retryable=False)
            mime_type = mimetypes.guess_type(os.path.basename(resolved))[0] or "audio/mpeg"
            with open(resolved, "rb") as f:
                raw = f.read()
            audio_value = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"
        references = [{"audio": audio_value, "text": reference_text}]

    out = await run_capability(
        "tts", "语音合成", provider=provider,
        text=text, voice=voice, references=references,
        emotion=emotion, speed=speed, pitch=pitch, language_boost=language_boost,
    )
    if out.get("success") and isinstance(out.get("audio_bytes"), bytes):
        audio_bytes = out.pop("audio_bytes")
        out["file_path"] = utils.save_audio(audio_bytes)
        out["size_bytes"] = len(audio_bytes)
    return _dumps(out)


# ==================================================================
# 音色管理（MiniMax 协议/模块）
# ==================================================================

@tool(name="clone_voice", group="media", timeout=300.0)
async def clone_voice(
    audio_path: str,
    voice_id: str,
    preview_text: str = "",
    provider: str = "auto",
) -> str:
    """音色复刻：用一段音频克隆声音，之后可在 text_to_voice 的 voice 参数中使用该 voice_id。

    Args:
        audio_path: 克隆源音频（本地路径或 URL，mp3/m4a/wav，10 秒~5 分钟，≤20MB）
        voice_id: 自定义音色 ID（8-256 字符，字母开头，可含数字/横线/下划线）
        preview_text: 可选试听文本（克隆后用新音色朗读，≤1000 字）
        provider: auto（默认）/ models / minimax
    """
    if not voice_id.strip():
        return tool_error("未提供 voice_id", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err

    if audio_path.startswith(("http://", "https://")):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60.0) as hc:
                resp = await hc.get(audio_path, follow_redirects=True)
                resp.raise_for_status()
            resolved = os.path.abspath(utils.save_audio(resp.content, fmt="mp3", prefix="clone_src"))
        except Exception as e:
            return error_from_exception(e, action="下载克隆源音频")
    else:
        try:
            resolved = utils.resolve_workspace_path(audio_path)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved):
            return tool_error(f"音频文件不存在: {audio_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)

    return _dumps(await run_capability(
        "voice_mgmt", "音色复刻", provider=provider,
        op="clone", resolved=resolved, voice_id=voice_id.strip(), preview_text=preview_text,
    ))


@tool(name="design_voice", group="media", timeout=300.0)
async def design_voice(prompt: str, preview_text: str = "", voice_id: str = "", provider: str = "auto") -> str:
    """音色设计：按文字描述生成新音色，返回 voice_id 与试听音频文件。

    Args:
        prompt: 音色描述（如"悬疑小说旁白，低沉磁性的男声"）
        preview_text: 试听文本（≤500 字，留空使用默认试听文本）
        voice_id: 可选自定义音色 ID，留空自动生成
        provider: auto（默认）/ models / minimax
    """
    if not prompt.strip():
        return tool_error("prompt 不能为空", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    if not preview_text.strip():
        preview_text = "你好，这是一段测试语音，用于预览音色效果。"

    out = await run_capability(
        "voice_mgmt", "音色设计", provider=provider,
        op="design", prompt=prompt, preview_text=preview_text, voice_id=voice_id.strip(),
    )
    if out.get("success"):
        trial = out.pop("trial_audio_bytes", None)
        if isinstance(trial, bytes):
            out["preview_file_path"] = utils.save_audio(trial)
        out.setdefault("hint", f"音色 '{out.get('voice_id')}' 已生成，可在 text_to_voice 的 voice 参数中使用")
    return _dumps(out)


@tool(name="list_voices", group="media")
async def list_voices(voice_type: str = "all", provider: str = "auto") -> str:
    """查询可用音色列表（系统音色/复刻音色/设计音色）。

    Args:
        voice_type: system / voice_cloning / voice_generation / all
        provider: auto（默认）/ models / minimax
    """
    err = _check_provider(provider)
    if err:
        return err
    out = await run_capability(
        "voice_mgmt", "音色查询", provider=provider,
        op="list", voice_type=voice_type.strip() or "all",
    )
    if out.get("success"):
        # 输出裁剪：每类最多 30 条、仅保留关键字段，避免上下文膨胀
        for category in ("system_voice", "voice_cloning", "voice_generation"):
            voices = out.get(category)
            if isinstance(voices, list):
                out[category] = {
                    "count": len(voices),
                    "voices": [
                        {k: v for k, v in voice.items() if k in ("voice_id", "voice_name", "description", "created_time")}
                        for voice in voices[:30]
                    ],
                }
        out.setdefault("hint", "可用 media_config(\"set\", \"default_voice\", \"<voice_id>\") 将某个音色设为默认音色")
    return _dumps(out)


@tool(name="delete_voice", group="media")
async def delete_voice(voice_id: str, voice_type: str = "voice_cloning", provider: str = "auto") -> str:
    """删除复刻/设计的音色（不可恢复）。

    Args:
        voice_id: 要删除的音色 ID
        voice_type: voice_cloning（复刻）或 voice_generation（设计）
        provider: auto（默认）/ models / minimax
    """
    if not voice_id.strip():
        return tool_error("未提供 voice_id", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await run_capability(
        "voice_mgmt", "音色删除", provider=provider,
        op="delete", voice_id=voice_id.strip(), voice_type=voice_type.strip() or "voice_cloning",
    ))


# ==================================================================
# 音乐生成（MiniMax 协议）
# ==================================================================

@tool(name="generate_music", group="media", timeout=300.0)
async def generate_music(
    prompt: str = "",
    lyrics: str = "",
    is_instrumental: bool = False,
    provider: str = "auto",
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
        provider: auto（默认）/ models
    """
    if not prompt.strip() and not lyrics.strip():
        return tool_error("prompt 与 lyrics 至少提供一项",
                          cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err

    out = await run_capability(
        "music", "音乐生成", provider=provider,
        op="generate", prompt=prompt, lyrics=lyrics, is_instrumental=bool(is_instrumental),
    )
    if out.get("success") and isinstance(out.get("audio_bytes"), bytes):
        audio_bytes = out.pop("audio_bytes")
        out["file_path"] = utils.save_audio(audio_bytes)
        out["size_bytes"] = len(audio_bytes)
    return _dumps(out)


@tool(name="generate_lyrics", group="media")
async def generate_lyrics(
    prompt: str = "",
    lyrics: str = "",
    title: str = "",
    mode: str = "write_full_song",
    provider: str = "auto",
) -> str:
    """歌词生成：写整首歌词或修改已有歌词（结果可直接用于 generate_music）。

    Args:
        prompt: 歌曲主题/风格描述（≤2000 字）
        lyrics: 已有歌词（mode=edit 时必填，≤3500 字）
        title: 保留的歌名（可选）
        mode: write_full_song（写整首）或 edit（修改已有歌词）
        provider: auto（默认）/ models
    """
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await run_capability(
        "music", "歌词生成", provider=provider,
        op="lyrics", prompt=prompt, lyrics=lyrics, title=title, mode=mode.strip() or "write_full_song",
    ))


# ==================================================================
# 视频生成
# ==================================================================

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
    provider: str = "auto",
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
        duration: 视频时长（秒），0 表示用媒体库配置默认或模型默认（MiniMax-H3: 4-15，Hailuo: 6 或 10）
        resolution: 分辨率，留空用媒体库配置默认；如 "2K"（MiniMax-H3）或 "768P"/"1080P"（Hailuo）
        ratio: 画面比例，如 "16:9"/"9:16"（仅 MiniMax-H3 文生视频有效）
        style: 可选风格预设名（见媒体库配置的 style_presets）或自定义风格描述
        provider: auto（默认）/ models
    """
    err = _check_provider(provider)
    if err:
        return err
    prompt = apply_style(prompt, style)
    duration = duration or int(get_default("video_duration", 0) or 0)
    resolution = resolution or str(get_default("video_resolution", ""))

    try:
        first_frame = utils.to_image_value(first_frame_image or image_url) if (first_frame_image or image_url) else ""
        last_frame = utils.to_image_value(last_frame_image) if last_frame_image else ""
        subjects = [utils.to_image_value(item) for item in utils.parse_subject_reference(subject_reference)]
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                          hint="请使用工作目录（workspace）内的路径")
    except FileNotFoundError as e:
        return tool_error(str(e), cause=ErrorCause.NOT_FOUND, retryable=False)

    out = await run_capability(
        "video", "视频生成", provider=provider,
        op="generate", prompt=prompt,
        first_frame_image=first_frame, last_frame_image=last_frame,
        subject_reference=subjects, duration=duration, resolution=resolution, ratio=ratio,
    )
    if out.get("success") and out.get("video_url"):
        out["file_path"] = await utils.save_video(out["video_url"])
    return _dumps(out)


# ==================================================================
# 视频任务管理（MiniMax 协议）
# ==================================================================

@tool(name="query_video_task", group="media", timeout=300.0)
async def query_video_task(task_id: str, download: bool = True, provider: str = "auto") -> str:
    """查询视频生成任务状态；任务成功时可下载视频到本地并返回文件路径。

    Args:
        task_id: 视频任务 ID（MiniMax 平台任务，由创建任务响应或任务列表获得）
        download: 任务成功时是否下载视频到本地（默认是）
        provider: auto（默认）/ models
    """
    if not task_id.strip():
        return tool_error("未提供 task_id", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    from entities._sdk import coerce_bool_arg
    download = coerce_bool_arg(download, True)

    out = await run_capability(
        "video", "视频任务查询", provider=provider, op="query", task_id=task_id.strip(),
    )
    if out.get("success") and out.get("status") == "succeeded" and download and out.get("video_url"):
        out["file_path"] = await utils.save_video(out["video_url"])
    return _dumps(out)


@tool(name="list_video_tasks", group="media")
async def list_video_tasks(page_num: int = 1, page_size: int = 20, status: str = "", provider: str = "auto") -> str:
    """分页查询近 7 天的视频生成任务列表（仅 MiniMax-H3 协议支持）。

    Args:
        page_num: 页码，从 1 开始
        page_size: 每页条数
        status: 可选状态过滤：queued/running/succeeded/failed/cancelled/expired
        provider: auto（默认）/ models
    """
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await run_capability(
        "video", "视频任务列表", provider=provider,
        op="list", page_num=max(1, int(page_num)), page_size=max(1, int(page_size)), status=status.strip(),
    ))


@tool(name="cancel_video_task", group="media")
async def cancel_video_task(task_id: str, provider: str = "auto") -> str:
    """取消排队中的视频任务（不计费）或删除已终结的任务记录（仅 MiniMax-H3 协议支持）。

    Args:
        task_id: 视频任务 ID
        provider: auto（默认）/ models
    """
    if not task_id.strip():
        return tool_error("未提供 task_id", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await run_capability(
        "video", "视频任务取消", provider=provider, op="cancel", task_id=task_id.strip(),
    ))


# ==================================================================
# 图片生成
# ==================================================================

@tool(name="generate_image", group="media", tags=["media:image_gen"])
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

    reference_image 非空时转为人物参考图生图（保持人物特征，仅 minimax 模块支持）。

    Args:
        prompt: 图片内容的文字描述
        image_size: 图片尺寸，留空用媒体库配置默认；支持像素格式 "1024x1024"、"1664x928"
            或比例格式 "1:1"/"16:9"/"9:16"（minimax 模块按最近比例映射）
        n: 生成数量 1~9（仅 minimax 模块生效，models 链由模型决定）
        num_inference_steps: 推理步数，默认 20（仅 models 链生效），越高越精细但更慢
        style: 可选风格预设名（见媒体库配置的 style_presets，如 nekomimi_maid）或自定义风格描述
        reference_image: 人物参考照片的本地路径或 URL（非空=人物参考图生图，仅 minimax 模块）
        provider: auto（默认）/ models / minimax
    """
    err = _check_provider(provider)
    if err:
        return err
    prompt = apply_style(prompt, style)
    image_size = image_size or str(get_default("image_size", "1024x1024"))
    n = min(max(1, int(n)), 9)

    out = await run_capability(
        "image_gen", "图片生成", provider=provider,
        prompt=prompt, image_size=image_size, n=n,
        num_inference_steps=num_inference_steps, reference_image=reference_image,
    )
    if out.get("success") and isinstance(out.get("image_results"), list):
        out["file_paths"] = await utils.save_images(out.pop("image_results"))
        if n > 1 and out.get("provider") == "models":
            out["note"] = "n 参数仅 minimax 模块生效，models 链生成数量由模型决定"
    return _dumps(out)


# ==================================================================
# 图片编辑
# ==================================================================

@tool(name="edit_image", group="media", tags=["media:image_edit"])
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
        provider: auto（默认）/ models
    """
    err = _check_provider(provider)
    if err:
        return err
    if image_path.startswith(("http://", "https://")):
        resolved_image = image_path
    else:
        try:
            resolved_image = utils.resolve_workspace_path(image_path)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved_image):
            return tool_error(f"图片不存在: {image_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=resolved_image)

    out = await run_capability(
        "image_edit", "图片编辑", provider=provider,
        image_path=resolved_image, prompt=prompt, num_inference_steps=num_inference_steps,
    )
    if out.get("success") and isinstance(out.get("image_results"), list):
        out["file_paths"] = await utils.save_images(out.pop("image_results"))
        out["source_image"] = image_path
    return _dumps(out)


# ==================================================================
# 媒体库配置管理
# ==================================================================

@tool(name="media_config", group="media")
async def media_config(action: str = "get", key: str = "", value: str = "") -> str:
    """查看或修改媒体库配置，查询媒体能力矩阵与 provider 状态。

    典型用法：
    - 规划媒体任务前先 capabilities 查当前可用能力与调用示例
    - design_voice/clone_voice 创建音色后，set default_voice <voice_id> 设为默认音色

    Args:
        action: get（全部配置，默认）/ capabilities（能力矩阵：工具选型+参数+示例+实时可用状态）/
            providers（各 provider 能力与配置状态）/ set（修改指定键）
        key: set 时必填。可选：default_voice / default_reference_audio / default_reference_text /
            defaults.image_size / defaults.video_resolution / defaults.video_duration /
            style_presets.<预设名>（value 为空=删除该预设）/
            provider_priority.<能力名>（value 为 JSON 数组如 '["models","minimax"]'，
            能力名: vision/asr/tts/voice_mgmt/music/video/image_gen/image_edit/rerank）
        value: set 时必填，配置值（provider_priority 用 JSON 数组字符串）
    """
    from . import config as media_config_mod
    action = action.strip().lower() or "get"
    if action == "get":
        return _dumps({"success": True, "config": media_config_mod.load_config()})
    if action == "providers":
        from .providers import provider_status
        return _dumps({
            "success": True,
            "providers": provider_status(),
            "provider_priority": media_config_mod.load_config().get("provider_priority", {}),
        })
    if action == "capabilities":
        from .capabilities import CAPABILITY_GUIDE
        from .providers import get_provider
        matrix: Dict[str, Any] = {}
        for cap, guide in CAPABILITY_GUIDE.items():
            chain = media_config_mod.provider_chain(cap)
            providers_info = []
            available = False
            for name in chain:
                impl = get_provider(name)
                if impl is None or cap not in impl.capabilities:
                    providers_info.append({"name": name, "configured": False, "note": "不支持该能力"})
                    continue
                try:
                    ready = impl.is_configured(cap)
                except Exception:
                    ready = False
                providers_info.append({"name": name, "configured": ready})
                available = available or ready
            matrix[cap] = {
                **guide,
                "chain": chain,
                "available": available,
                "providers": providers_info,
            }
        return _dumps({
            "success": True,
            "capabilities": matrix,
            "hint": "available=false 的能力说明链上 provider 均未配置，可用 providers 动作查看详情，"
                    "或引导主人在媒体库配置面板/模型配置中补齐",
        })
    if action != "set":
        return tool_error(f"未知操作: {action}", cause=ErrorCause.PARAM, retryable=False,
                          hint="可选: get / capabilities / providers / set")
    if not key.strip():
        return tool_error("set 操作必须提供 key", cause=ErrorCause.PARAM, retryable=False)
    key = key.strip()

    parsed: Any = value
    if key.startswith("provider_priority."):
        from .providers.base import ALL_CAPABILITIES
        cap = key.split(".", 1)[1].strip()
        if cap not in ALL_CAPABILITIES:
            return tool_error(f"未知能力名: {cap}", cause=ErrorCause.PARAM, retryable=False,
                              hint=f"可选: {' / '.join(ALL_CAPABILITIES)}")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            # 兼容逗号分隔写法
            parsed = [p.strip() for p in value.split(",") if p.strip()]
        unknown = [p for p in parsed if isinstance(p, str) and p not in PROVIDER_NAMES]
        if not isinstance(parsed, list) or unknown:
            return tool_error(
                f"provider_priority 值非法: {value}",
                cause=ErrorCause.PARAM, retryable=False,
                hint=f"provider 可选: {' / '.join(PROVIDER_NAMES)}，示例 '[\"models\",\"minimax\"]'",
            )

    try:
        saved = media_config_mod.update_key(key, parsed)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    result: Dict[str, Any] = {"success": True, "key": key}
    top = key.split(".", 1)[0]
    result["value"] = saved.get(top)
    if key == "default_voice":
        result["hint"] = "默认音色已更新，text_to_voice 不传 voice 时将使用该音色"
    return _dumps(result)


# ==================================================================
# 文档重排序
# ==================================================================

@tool(name="rerank_search", group="media")
async def rerank_search(query: str, documents: str, provider: str = "auto") -> str:
    """按相关性对文档列表重新排序。documents 应为 JSON 字符串数组。

    Args:
        query: 查询语句
        documents: JSON 格式的文档字符串数组，如 '["文档1", "文档2"]'
        provider: auto（默认）/ models
    """
    err = _check_provider(provider)
    if err:
        return err
    try:
        doc_list = json.loads(documents)
        if not isinstance(doc_list, list):
            return tool_error("documents 必须是 JSON 字符串数组",
                              cause=ErrorCause.PARAM, retryable=False,
                              hint="请传入形如 '[\"文档1\", \"文档2\"]' 的 JSON 字符串")
    except json.JSONDecodeError:
        doc_list = [d.strip() for d in documents.split("\n") if d.strip()]

    out = await run_capability(
        "rerank", "文档重排序", provider=provider, query=query, documents=doc_list,
    )
    if out.get("success"):
        out.setdefault("query", query)
    return _dumps(out)
