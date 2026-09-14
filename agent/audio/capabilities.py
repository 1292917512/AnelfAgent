"""声音能力路由 — 一次性语音合成、音色管理、音乐生成的提供者分发。

能力集合：
- tts：文字转语音（整段合成，产物为音频字节；实时低延迟流式合成走
  agent.tts 注册表，两者互补）
- voice_mgmt：音色复刻/设计/查询/删除
- music：音乐/歌曲生成与歌词创作

内部模型利用：内置 ``models`` 提供者桥接模型配置（llm_clients.json）中
tts/music 类型的模型优先级链；第三方组件经
entities._sdk.register_sound_provider 挂入同一路由（即插即用）。
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

CAP_TTS = "tts"
CAP_VOICE_MGMT = "voice_mgmt"
CAP_MUSIC = "music"

SOUND_CAPABILITIES = (CAP_TTS, CAP_VOICE_MGMT, CAP_MUSIC)

# 能力 → iter_media_for_type 的模型类型
_CAPABILITY_MODEL_TYPE = {
    CAP_TTS: "tts",
    CAP_VOICE_MGMT: "tts",
    CAP_MUSIC: "music",
}

_LOG_TAG = "声音"


def _mgr() -> Any:
    from agent.llm import get_llm_manager
    return get_llm_manager()


class ModelsSoundProvider:
    """内部模型链提供者：能力路由到 llm_clients.json 对应类型的模型优先级链。"""

    name = "models"
    capabilities = frozenset({CAP_TTS, CAP_VOICE_MGMT, CAP_MUSIC})

    def is_configured(self, capability: str) -> bool:
        try:
            model_type = _CAPABILITY_MODEL_TYPE.get(capability, "")
            return bool(model_type) and bool(_mgr().iter_media_for_type(model_type))
        except Exception as e:
            log(f"models 提供者可用性检查失败: {e}", "DEBUG", tag=_LOG_TAG)
            return False

    def status_details(self, capability: str) -> Dict[str, Any]:
        return {}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        model_type = _CAPABILITY_MODEL_TYPE.get(capability)
        if not model_type:
            raise CapabilityNotSupported(f"models 提供者不支持能力 '{capability}'")
        dispatch: Dict[str, Callable[..., Awaitable[Any]]] = {
            CAP_TTS: self._tts,
            CAP_VOICE_MGMT: self._voice_mgmt,
            CAP_MUSIC: self._music,
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
    # 各能力的单模型调用
    # ------------------------------------------------------------------

    async def _tts(
        self,
        model: str,
        client: Any,
        *,
        text: str,
        voice: str,
        references: Optional[List[Dict[str, str]]],
        emotion: str,
        speed: float,
        pitch: int,
        language_boost: str,
    ) -> Dict[str, Any]:
        audio_bytes = await client.text_to_speech(
            text, model=model, voice=voice, references=references,
            emotion=emotion, speed=speed or None, pitch=pitch or None,
            language_boost=language_boost,
        )
        return {"audio_bytes": audio_bytes}

    async def _voice_mgmt(self, model: str, client: Any, *, op: str, **kwargs: Any) -> Dict[str, Any]:
        if op == "clone":
            result = await client.voice_clone(
                kwargs["resolved"], voice_id=kwargs["voice_id"],
                preview_text=kwargs.get("preview_text", ""), model=model,
            )
            return {"voice_id": kwargs["voice_id"], **result}
        if op == "design":
            result = await client.voice_design(
                prompt=kwargs["prompt"], preview_text=kwargs["preview_text"],
                voice_id=kwargs.get("voice_id", ""),
            )
            out: Dict[str, Any] = {"voice_id": result.get("voice_id", "")}
            trial = result.get("trial_audio")
            if trial:
                out["trial_audio_bytes"] = trial
            return out
        if op == "list":
            return await client.list_voices(kwargs.get("voice_type") or "all")
        if op == "delete":
            return await client.delete_voice(kwargs["voice_id"], kwargs.get("voice_type") or "voice_cloning")
        raise CapabilityNotSupported(f"未知音色管理操作: {op}")

    async def _music(self, model: str, client: Any, *, op: str, **kwargs: Any) -> Dict[str, Any]:
        if op == "generate":
            result = await client.generate_music(
                model=model, prompt=kwargs.get("prompt", ""),
                lyrics=kwargs.get("lyrics", ""),
                is_instrumental=bool(kwargs.get("is_instrumental", False)),
            )
            return {"audio_bytes": result.audio, "extra_info": result.extra_info}
        if op == "lyrics":
            return await client.generate_lyrics(
                mode=kwargs.get("mode") or "write_full_song",
                prompt=kwargs.get("prompt", ""), lyrics=kwargs.get("lyrics", ""),
                title=kwargs.get("title", ""),
            )
        raise CapabilityNotSupported(f"未知音乐操作: {op}")


# ------------------------------------------------------------------
# 路由单例与配置
# ------------------------------------------------------------------

_router: Optional[CapabilityRouter] = None


def get_sound_router() -> CapabilityRouter:
    """声音能力路由器单例（内部 models 提供者随首次获取注册）。"""
    global _router
    if _router is None:
        _router = CapabilityRouter(
            config_key="sound_provider_priority",
            default_chain=["models"],
            log_tag=_LOG_TAG,
        )
        _router.register(ModelsSoundProvider())
    return _router


def reset_sound_router() -> None:
    """重置路由器（测试用）。"""
    global _router
    _router = None
