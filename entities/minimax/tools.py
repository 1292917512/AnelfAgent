"""MiniMax 多媒体工具实体 — 语音合成、图片生成、音色克隆与管理。

独立于现有 media 工具，直接对接 MiniMax 平台 API。
API Key 通过 entities/minimax/config.json 配置。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from typing import Any

from entities._sdk import tool, entity

entity("minimax", "MiniMax 多媒体 — 语音合成、图片生成、音色克隆与管理")


def _get_workspace_root() -> str:
    try:
        from core.config import ConfigManager
        return ConfigManager.get("workspace_root", "workspace")
    except Exception:
        return "workspace"


def _resolve_workspace_path(path: str) -> str:
    """解析可能相对于 workspace 或 CWD 的路径。"""
    if not path:
        return ""
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
    return candidate


def _client() -> "MiniMaxClient":
    from entities.minimax.client import MiniMaxClient
    return MiniMaxClient()


def _ensure_configured() -> str | None:
    """检查 API Key 是否已配置，未配置返回错误 JSON 字符串。"""
    c = _client()
    if not c.configured:
        return json.dumps({
            "error": "MiniMax 未配置 API Key，请编辑 entities/minimax/config.json 填入 api_key",
        }, ensure_ascii=False)
    return None


def _save_audio(audio_bytes: bytes, fmt: str = "mp3") -> str:
    """保存音频到 workspace/uploads/audio/ 目录，返回相对路径。"""
    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "audio")
    os.makedirs(save_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    fname = f"minimax_tts_{ts}.{fmt}"
    fpath = os.path.join(save_dir, fname)
    with open(fpath, "wb") as f:
        f.write(audio_bytes)
    return os.path.relpath(fpath, os.getcwd()).replace("\\", "/")


async def _save_images(image_urls: list[str]) -> list[str]:
    """下载图片 URL 保存到 workspace/uploads/image/ 目录，返回相对路径列表。"""
    import httpx

    ws_root = _get_workspace_root()
    save_dir = os.path.join(os.path.abspath(ws_root), "uploads", "image")
    os.makedirs(save_dir, exist_ok=True)
    saved: list[str] = []
    for i, src in enumerate(image_urls):
        ts = int(time.time() * 1000)
        if src.startswith("data:image/"):
            header, b64 = src.split(",", 1)
            img_bytes = base64.b64decode(b64)
            ext = ".png" if "png" in header else ".jpg"
        else:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(src, follow_redirects=True)
                resp.raise_for_status()
                img_bytes = resp.content
                ct = resp.headers.get("content-type", "image/png")
                ext = ".png" if "png" in ct else ".jpg"
        fname = f"minimax_img_{ts}_{i}{ext}"
        fpath = os.path.join(save_dir, fname)
        with open(fpath, "wb") as f:
            f.write(img_bytes)
        rel = os.path.relpath(fpath, os.getcwd()).replace("\\", "/")
        saved.append(rel)
    return saved


def _load_reference_image(image_path: str) -> str:
    """将本地图片路径转为 base64 Data URL，URL 直接返回。"""
    if image_path.startswith(("http://", "https://", "data:image/")):
        return image_path
    resolved = _resolve_workspace_path(image_path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")
    mime = mimetypes.guess_type(os.path.basename(resolved))[0] or "image/jpeg"
    with open(resolved, "rb") as f:
        raw = f.read()
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


# ==================================================================
# 语音合成 TTS
# ==================================================================

@tool(name="minimax_tts", group="minimax", timeout=120.0)
async def minimax_tts(
    text: str,
    voice_id: str = "",
    speed: float = 1.0,
    emotion: str = "",
    pitch: int = 0,
    language_boost: str = "",
) -> str:
    """使用 MiniMax 将文字转为语音音频，支持多种音色和情绪控制，返回音频文件路径。

    Args:
        text: 要转换为语音的文字内容（上限 10000 字符，超过 3000 字符建议分段）
        voice_id: 音色 ID（如 male-qn-qingse），留空使用默认音色
        speed: 语速，范围 0.5~2.0，默认 1.0
        emotion: 情绪控制，可选 happy/sad/angry/fearful/disgusted/surprised/calm
        pitch: 语调，范围 -12~12，0 为原音色
        language_boost: 语种增强，可选 Chinese/English/Japanese/auto 等，留空自动识别
    """
    err = _ensure_configured()
    if err:
        return err
    try:
        c = _client()
        audio_bytes = await c.text_to_speech(
            text,
            voice_id=voice_id,
            speed=speed,
            emotion=emotion,
            pitch=pitch,
            language_boost=language_boost,
        )
        path = _save_audio(audio_bytes)
        return json.dumps({
            "success": True,
            "file_path": path,
            "size_bytes": len(audio_bytes),
            "voice_id": voice_id or "(default)",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 文生图
# ==================================================================

@tool(name="minimax_generate_image", group="minimax", tags=["media:image_gen"], timeout=120.0)
async def minimax_generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    n: int = 1,
    model: str = "",
) -> str:
    """使用 MiniMax 根据文字描述生成图片，支持多种宽高比，返回图片文件路径。

    Args:
        prompt: 图片内容的文字描述（上限 1500 字符）
        aspect_ratio: 宽高比，可选 1:1/16:9/4:3/3:2/2:3/3:4/9:16
        n: 生成数量，1~9 张
        model: 模型版本，可选 image-01（默认）或 image-01-live
    """
    err = _ensure_configured()
    if err:
        return err
    try:
        c = _client()
        image_urls = await c.generate_image(
            prompt, model=model, aspect_ratio=aspect_ratio, n=n,
        )
        if not image_urls:
            return json.dumps({"error": "未返回图片结果"}, ensure_ascii=False)
        saved = await _save_images(image_urls)
        return json.dumps({
            "success": True,
            "file_paths": saved,
            "prompt": prompt,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 图生图（人物参考）
# ==================================================================

@tool(name="minimax_image_to_image", group="minimax", tags=["media:image"], timeout=120.0)
async def minimax_image_to_image(
    prompt: str,
    reference_image: str,
    aspect_ratio: str = "1:1",
    n: int = 1,
) -> str:
    """使用 MiniMax 基于参考人物照片生成新图片（保持人物特征一致），返回图片文件路径。

    适用于：使用某人的照片，生成该人在不同场景下的图片。
    参考图建议使用单人正面照，支持 JPG/JPEG/PNG 格式（小于 10MB）。

    Args:
        reference_image: 参考人物照片的本地路径或 URL
        prompt: 目标图片的文字描述
        aspect_ratio: 宽高比，可选 1:1/16:9/4:3/3:2/2:3/3:4/9:16
        n: 生成数量，1~9 张
    """
    err = _ensure_configured()
    if err:
        return err
    try:
        ref = _load_reference_image(reference_image)
        c = _client()
        image_urls = await c.image_to_image(
            prompt, ref, aspect_ratio=aspect_ratio, n=n,
        )
        if not image_urls:
            return json.dumps({"error": "未返回图片结果"}, ensure_ascii=False)
        saved = await _save_images(image_urls)
        return json.dumps({
            "success": True,
            "file_paths": saved,
            "prompt": prompt,
            "reference_image": reference_image,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 音色克隆
# ==================================================================

@tool(name="minimax_clone_voice", group="minimax", tags=["media:voice", "media:audio"], timeout=120.0)
async def minimax_clone_voice(
    audio_path: str,
    voice_id: str,
    preview_text: str = "",
) -> str:
    """使用 MiniMax 克隆音色：上传音频文件，注册为可复用的音色 ID，后续可在语音合成中使用。

    要求：音频 10 秒~5 分钟，支持 mp3/m4a/wav 格式，小于 20MB。
    注册的 voice_id 需 8~256 字符，字母开头，允许数字/字母/-/_。
    克隆后的音色 7 天内未使用会被自动删除。

    Args:
        audio_path: 待克隆的音频文件路径或 URL
        voice_id: 自定义音色 ID（如 my-custom-voice-01）
        preview_text: 试听文本（可选，提供后返回试听信息）
    """
    err = _ensure_configured()
    if err:
        return err
    try:
        c = _client()
        is_url = audio_path.startswith(("http://", "https://"))
        if is_url:
            import httpx
            async with httpx.AsyncClient(timeout=60.0) as hc:
                resp = await hc.get(audio_path, follow_redirects=True)
                resp.raise_for_status()
                file_data = resp.content
            filename = audio_path.rsplit("/", 1)[-1].split("?")[0] or "audio.mp3"
        else:
            resolved = _resolve_workspace_path(audio_path)
            if not os.path.exists(resolved):
                return json.dumps({"error": f"音频文件不存在: {audio_path}"}, ensure_ascii=False)
            with open(resolved, "rb") as f:
                file_data = f.read()
            filename = os.path.basename(resolved)

        file_id = await c.upload_file(file_data, filename, purpose="voice_clone")
        result = await c.voice_clone(file_id, voice_id, preview_text=preview_text)
        return json.dumps({
            "success": True,
            "voice_id": result["voice_id"],
            "file_id": file_id,
            "has_demo": bool(result.get("demo_audio")),
            "hint": f"音色 '{voice_id}' 已注册，可在 minimax_tts 的 voice_id 参数中使用",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 文生音色
# ==================================================================

@tool(name="minimax_design_voice", group="minimax", timeout=120.0)
async def minimax_design_voice(
    prompt: str,
    preview_text: str = "",
    voice_id: str = "",
) -> str:
    """使用 MiniMax 根据文字描述生成新音色。描述可包含性别、年龄、风格等特征。

    示例 prompt："讲述悬疑故事的播音员，声音低沉富有磁性，语速时快时慢"。
    生成的音色可在 minimax_tts 的 voice_id 参数中使用。

    Args:
        prompt: 音色描述（描述声音特征）
        preview_text: 试听文本（上限 500 字符），提供后可验证音色效果
        voice_id: 自定义音色 ID（可选，不提供则自动生成）
    """
    err = _ensure_configured()
    if err:
        return err
    if not preview_text:
        preview_text = "你好，这是一段测试语音，用于预览音色效果。"
    try:
        c = _client()
        result = await c.voice_design(prompt, preview_text, voice_id=voice_id)
        return json.dumps({
            "success": True,
            "voice_id": result["voice_id"],
            "hint": f"音色 '{result['voice_id']}' 已生成，可在 minimax_tts 的 voice_id 参数中使用",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 查询音色列表
# ==================================================================

@tool(name="minimax_list_voices", group="minimax")
async def minimax_list_voices(
    voice_type: str = "all",
) -> str:
    """查询 MiniMax 账号下可用的音色列表，包括系统音色、克隆音色和生成音色。

    Args:
        voice_type: 查询类型，可选 system（系统）/voice_cloning（克隆）/voice_generation（生成）/all（全部）
    """
    err = _ensure_configured()
    if err:
        return err
    try:
        c = _client()
        result = await c.get_voices(voice_type)
        summary: dict[str, Any] = {"success": True}
        for category, voices in result.items():
            if isinstance(voices, list):
                summary[category] = {
                    "count": len(voices),
                    "voices": [
                        {k: v for k, v in voice.items() if k in ("voice_id", "voice_name", "description", "created_time")}
                        for voice in voices[:30]
                    ],
                }
        return json.dumps(summary, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 删除音色
# ==================================================================

@tool(name="minimax_delete_voice", group="minimax")
async def minimax_delete_voice(
    voice_id: str,
    voice_type: str = "voice_cloning",
) -> str:
    """删除 MiniMax 中已注册的音色（仅支持删除克隆音色或生成音色，不可删除系统音色）。

    Args:
        voice_id: 要删除的音色 ID
        voice_type: 音色类型，可选 voice_cloning（克隆）或 voice_generation（生成）
    """
    err = _ensure_configured()
    if err:
        return err
    try:
        c = _client()
        result = await c.delete_voice(voice_id, voice_type)
        return json.dumps({
            "success": True,
            "deleted_voice_id": result["voice_id"],
            "created_time": result.get("created_time", ""),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
