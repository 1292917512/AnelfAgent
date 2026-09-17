"""声音生成工具 — 文件转写、一次性语音合成、音色管理、音乐生成与声音能力配置。

接口层：参数归一 → 沙箱校验 → 能力路由（get_sound_router / 音频服务 ASR 链）；
产物（音频）统一落盘 workspace/uploads/ 并返回相对路径。

provider 参数：auto（默认，按配置优先级链自动路由+失败降级）或指定
提供者名；可用值与能力优先级经 sound_config(action="providers") 查看。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from typing import Any, Dict, List, Optional

from agent.audio.capabilities import SOUND_CAPABILITIES, get_sound_router
from agent.utils import workspace as ws
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import deferred_tool

_group = "audio"


def _dumps(out: Dict[str, Any]) -> str:
    return json.dumps(out, ensure_ascii=False)


def _check_provider(provider: str) -> Optional[str]:
    """校验 provider 参数合法性，非法返回错误 JSON。"""
    names = get_sound_router().names()
    if provider and provider != "auto" and provider not in names:
        return tool_error(
            f"未知提供者: {provider}",
            cause=ErrorCause.PARAM, retryable=False,
            hint=f"可选: auto / {' / '.join(names)}",
        )
    return None


# ==================================================================
# 语音识别 ASR
# ==================================================================

@deferred_tool(name="voice_to_text", group=_group, tags=["always", "media:voice", "media:audio"], timeout=300.0)
async def voice_to_text(audio_source: str = "", **kwargs: str) -> str:
    """将语音/音频文件转写为文字（ASR 语音识别）。支持本地文件路径或 URL。

    经声音 ASR 优先级链转写（本地组件优先，云端 asr 模型链兜底）。

    Args:
        audio_source: 音频文件的本地路径（如 workspace/uploads/voice/xxx.ogg）或 URL
    """
    if not audio_source:
        audio_source = kwargs.get("path", "") or kwargs.get("file_path", "") or kwargs.get("url", "")
    if not audio_source:
        return tool_error("未提供音频路径或 URL", cause=ErrorCause.PARAM, retryable=False)

    if audio_source.startswith(("http://", "https://")):
        from agent.channel.media import download_to_uploads
        from agent.channel.schemas import SegmentType
        resolved = await download_to_uploads(audio_source, SegmentType.AUDIO)
        if not resolved:
            return tool_error(f"音频下载失败: {audio_source[:100]}",
                              cause=ErrorCause.NETWORK, retryable=True)
    else:
        try:
            resolved = ws.resolve_workspace_path(audio_source)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved):
            return tool_error(f"文件不存在: {audio_source}", cause=ErrorCause.NOT_FOUND,
                              retryable=False, resolved=resolved)

    from agent.audio.service import AudioNotConfigured, get_audio_service
    try:
        segments = await get_audio_service().transcribe(resolved)
    except AudioNotConfigured as e:
        return tool_error(str(e), cause=ErrorCause.CONFIG, retryable=False,
                          hint="配置 FunASR 组件或 asr 类型模型后可用")
    except Exception as e:
        return error_from_exception(e, action="语音识别")
    text = "\n".join(s.get("text", "").strip() for s in segments if s.get("text", "").strip())
    return _dumps({"success": True, "text": text, "segments": len(segments)})


# ==================================================================
# 语音合成 TTS
# ==================================================================

@deferred_tool(name="text_to_voice", group=_group, tags=["always", "media:voice", "media:audio"], timeout=300.0)
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
    1. 预置音色：voice 参数（可用 list_voices 查询各提供者音色）
    2. 声音克隆：reference_audio 参考音频 + reference_text 对应文字（仅 models 链 OpenAI 风格协议）

    两者都不传时，使用默认音色配置。超过 3000 字的长文本
    在支持的协议上自动走异步合成。

    Args:
        text: 要转换为语音的文字内容
        voice: 预置音色 ID
        reference_audio: 声音克隆的参考音频（URL 或本地路径），与 voice 互斥
        reference_text: 参考音频中的文字内容（克隆时必须提供）
        emotion: 情绪（MiniMax 协议）：happy/sad/angry/fearful/disgusted/surprised/calm/fluent
        speed: 语速 0.5~2.0，0 表示默认（MiniMax 协议）
        pitch: 语调 -12~12，0 表示原音色（MiniMax 协议）
        language_boost: 语种增强（MiniMax 协议）：Chinese/English/Japanese/auto 等
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    from agent.tts import default_voice
    from core.config import ConfigManager

    err = _check_provider(provider)
    if err:
        return err
    if not voice and not reference_audio:
        default_ref = str(ConfigManager.get("tts_default_reference_audio", "") or "")
        if default_ref:
            reference_audio = default_ref
            reference_text = reference_text or str(ConfigManager.get("tts_default_reference_text", "") or "")
        else:
            voice = default_voice()

    if reference_audio and not reference_text:
        return tool_error("使用声音克隆时必须提供 reference_text",
                          cause=ErrorCause.PARAM, retryable=False)

    references: Optional[List[Dict[str, str]]] = None
    if reference_audio:
        audio_value = reference_audio
        if not audio_value.startswith(("http://", "https://", "data:audio/")):
            try:
                resolved = ws.resolve_workspace_path(audio_value)
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

    out = await get_sound_router().run(
        "tts", "语音合成", provider=provider or "auto",
        text=text, voice=voice, references=references,
        emotion=emotion, speed=speed, pitch=pitch, language_boost=language_boost,
    )
    if out.get("success") and isinstance(out.get("audio_bytes"), bytes):
        audio_bytes = out.pop("audio_bytes")
        out["file_path"] = ws.save_audio(audio_bytes)
        out["size_bytes"] = len(audio_bytes)
    return _dumps(out)


# ==================================================================
# 音色管理
# ==================================================================

@deferred_tool(name="clone_voice", group=_group, tags=["core"], timeout=300.0)
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
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
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
            resolved = os.path.abspath(ws.save_audio(resp.content, fmt="mp3", prefix="clone_src"))
        except Exception as e:
            return error_from_exception(e, action="下载克隆源音频")
        source_url = audio_path  # 需公网 URL 的提供者（如百炼复刻）直用源链接
    else:
        source_url = ""
        try:
            resolved = ws.resolve_workspace_path(audio_path)
        except ValueError as e:
            return tool_error(str(e), cause=ErrorCause.PERMISSION, retryable=False,
                              hint="请使用工作目录（workspace）内的路径")
        if not os.path.exists(resolved):
            return tool_error(f"音频文件不存在: {audio_path}", cause=ErrorCause.NOT_FOUND,
                              retryable=False)

    return _dumps(await get_sound_router().run(
        "voice_mgmt", "音色复刻", provider=provider or "auto",
        op="clone", resolved=resolved, source_url=source_url,
        voice_id=voice_id.strip(), preview_text=preview_text,
    ))


@deferred_tool(name="design_voice", group=_group, tags=["core"], timeout=300.0)
async def design_voice(prompt: str, preview_text: str = "", voice_id: str = "", provider: str = "auto") -> str:
    """音色设计：按文字描述生成新音色，返回 voice_id 与试听音频文件。

    Args:
        prompt: 音色描述（如"悬疑小说旁白，低沉磁性的男声"）
        preview_text: 试听文本（≤500 字，留空使用默认试听文本）
        voice_id: 可选自定义音色 ID，留空自动生成
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    if not prompt.strip():
        return tool_error("prompt 不能为空", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    if not preview_text.strip():
        preview_text = "你好，这是一段测试语音，用于预览音色效果。"

    out = await get_sound_router().run(
        "voice_mgmt", "音色设计", provider=provider or "auto",
        op="design", prompt=prompt, preview_text=preview_text, voice_id=voice_id.strip(),
    )
    if out.get("success"):
        trial = out.pop("trial_audio_bytes", None)
        if isinstance(trial, bytes):
            out["preview_file_path"] = ws.save_audio(trial)
        out.setdefault("hint", f"音色 '{out.get('voice_id')}' 已生成，可在 text_to_voice 的 voice 参数中使用")
    return _dumps(out)


@deferred_tool(name="list_voices", group=_group, tags=["core"], concurrency_safe=True)
async def list_voices(voice_type: str = "all", provider: str = "auto") -> str:
    """查询可用音色列表（系统音色/复刻音色/设计音色）。

    Args:
        voice_type: system / voice_cloning / voice_generation / all
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    err = _check_provider(provider)
    if err:
        return err
    out = await get_sound_router().run(
        "voice_mgmt", "音色查询", provider=provider or "auto",
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
        out.setdefault("hint", "可用 sound_config(\"set\", \"default_voice\", \"<voice_id>\") 将某个音色设为默认音色")
    return _dumps(out)


@deferred_tool(name="delete_voice", group=_group, tags=["core"])
async def delete_voice(voice_id: str, voice_type: str = "voice_cloning", provider: str = "auto") -> str:
    """删除复刻/设计的音色（不可恢复）。

    Args:
        voice_id: 要删除的音色 ID
        voice_type: voice_cloning（复刻）或 voice_generation（设计）
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    if not voice_id.strip():
        return tool_error("未提供 voice_id", cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await get_sound_router().run(
        "voice_mgmt", "音色删除", provider=provider or "auto",
        op="delete", voice_id=voice_id.strip(), voice_type=voice_type.strip() or "voice_cloning",
    ))


# ==================================================================
# 音乐生成
# ==================================================================

@deferred_tool(name="generate_music", group=_group, tags=["always", "media:audio"], timeout=300.0)
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
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    if not prompt.strip() and not lyrics.strip():
        return tool_error("prompt 与 lyrics 至少提供一项",
                          cause=ErrorCause.PARAM, retryable=False)
    err = _check_provider(provider)
    if err:
        return err

    out = await get_sound_router().run(
        "music", "音乐生成", provider=provider or "auto",
        op="generate", prompt=prompt, lyrics=lyrics, is_instrumental=bool(is_instrumental),
    )
    if out.get("success") and isinstance(out.get("audio_bytes"), bytes):
        audio_bytes = out.pop("audio_bytes")
        out["file_path"] = ws.save_audio(audio_bytes, kind="music")
        out["size_bytes"] = len(audio_bytes)
    return _dumps(out)


@deferred_tool(name="generate_lyrics", group=_group, tags=["always"])
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
        provider: auto（默认，按配置链路由+失败自动降级）或指定提供者名
    """
    err = _check_provider(provider)
    if err:
        return err
    return _dumps(await get_sound_router().run(
        "music", "歌词生成", provider=provider or "auto",
        op="lyrics", prompt=prompt, lyrics=lyrics, title=title, mode=mode.strip() or "write_full_song",
    ))


# ==================================================================
# 声音能力配置管理
# ==================================================================

_SCALAR_KEYS = {
    "default_voice": "tts_default_voice",
    "realtime_voice": "realtime_tts_voice",
    "default_reference_audio": "tts_default_reference_audio",
    "default_reference_text": "tts_default_reference_text",
    "funasr_timeout": "funasr_timeout",
}


@deferred_tool(name="sound_config", group=_group, tags=["core"])
async def sound_config(action: str = "capabilities", key: str = "", value: str = "") -> str:
    """查看声音能力矩阵与提供者状态，或修改默认音色/FunASR 服务/优先级链。

    典型用法：
    - 规划声音任务前先 capabilities 查当前可用能力与调用示例
    - design_voice/clone_voice 创建音色后，set default_voice <voice_id> 设为默认音色
    - 转写/声纹不可用时，set funasr_endpoint http://<host>:<port> 配置本地转写服务

    Args:
        action: capabilities（能力矩阵：工具选型+参数+示例+实时可用状态，默认）/
            providers（各提供者能力与配置状态）/ get（全部声音配置）/ set（修改指定键）
        key: set 时必填。可选：default_voice（合成默认音色）/
            realtime_voice（实时通话默认音色，克隆音色 ID 可用）/
            default_reference_audio / default_reference_text /
            funasr_timeout（秒）/ provider_priority.<能力名>
            （value 为 JSON 数组如 '["models"]'，能力名: tts/voice_mgmt/music）
        value: set 时必填，配置值（provider_priority 用 JSON 数组字符串）
    """
    from core.config import ConfigManager
    from entities._sdk import save_config_value

    action = action.strip().lower() or "capabilities"
    router = get_sound_router()

    if action == "get":
        from entities.audiosync import client as funasr_client

        funasr_client.reset_probe_cache()
        return _dumps({"success": True, "config": {
            "provider_priority": ConfigManager.get("sound_provider_priority", {}),
            "default_voice": ConfigManager.get("tts_default_voice", ""),
            "default_reference_audio": ConfigManager.get("tts_default_reference_audio", ""),
            "default_reference_text": ConfigManager.get("tts_default_reference_text", ""),
            "realtime_voice": ConfigManager.get("realtime_tts_voice", ""),
            "funasr_timeout": ConfigManager.get("funasr_timeout", 120),
            "funasr_reachable": await funasr_client.probe_available(),
        }})
    if action == "providers":
        return _dumps({"success": True, **router.status(list(SOUND_CAPABILITIES))})
    if action == "capabilities":
        from agent.audio.guide import SOUND_CAPABILITY_GUIDE
        matrix: Dict[str, Any] = {}
        for cap, guide in SOUND_CAPABILITY_GUIDE.items():
            if cap == "asr":
                from agent.audio.providers import KIND_ASR, get_audio_registry
                providers_info = []
                for p in get_audio_registry().list(KIND_ASR):
                    try:
                        ready = await p.check_available()
                    except Exception:
                        ready = False
                    providers_info.append({"name": p.name, "configured": ready})
                matrix[cap] = {
                    **guide,
                    "chain": [p["name"] for p in providers_info],
                    "available": any(p["configured"] for p in providers_info),
                    "providers": providers_info,
                }
                continue
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
                    "或引导主人在声音页签/模型配置中补齐",
        })
    if action != "set":
        return tool_error(f"未知操作: {action}", cause=ErrorCause.PARAM, retryable=False,
                          hint="可选: capabilities / providers / get / set")
    if not key.strip():
        return tool_error("set 操作必须提供 key", cause=ErrorCause.PARAM, retryable=False)
    key = key.strip()

    if key in _SCALAR_KEYS:
        save_config_value(_SCALAR_KEYS[key], str(value))
        result: Dict[str, Any] = {"success": True, "key": key, "value": str(value)}
        if key == "default_voice":
            result["hint"] = "默认音色已更新，全部合成入口统一使用（通话未单独覆盖时同样生效）"
        if key == "funasr_endpoint":
            from entities.audiosync import client as funasr_client

            funasr_client.reset_probe_cache()
            reachable = await funasr_client.probe_available()
            result["reachable"] = reachable
            result["hint"] = (
                "服务在线，转写/流式转写/声纹提取即刻可用" if reachable
                else "地址已保存但服务不可达：请确认服务已启动、地址端口正确")
        return _dumps(result)

    if key.startswith("provider_priority."):
        cap = key.split(".", 1)[1].strip()
        if cap not in SOUND_CAPABILITIES:
            return tool_error(f"未知能力名: {cap}", cause=ErrorCause.PARAM, retryable=False,
                              hint=f"可选: {' / '.join(SOUND_CAPABILITIES)}")
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
        priority = dict(ConfigManager.get("sound_provider_priority", {}) or {})
        priority[cap] = list(parsed)
        save_config_value("sound_provider_priority", priority)
        return _dumps({"success": True, "key": key, "chain": router.chain(cap)})

    return tool_error(f"不支持的配置键: {key}", cause=ErrorCause.PARAM, retryable=False,
                      hint="可选: default_voice / realtime_voice / default_reference_audio / "
                           "default_reference_text / funasr_timeout / "
                           "provider_priority.<能力>")
