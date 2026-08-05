"""媒体能力 provider 基类与能力常量。

媒体库分层：tools.py（统一工具面）→ providers 路由（优先级链）→ 各 provider 实现。
- models provider：桥接 LLMManager，使用模型配置（llm_clients.json）中对应类型的模型
- minimax provider：桥接 entities/minimax 直连模块（可插拔，删除该目录即自动退出优先级链）

provider run() 的 kwargs 契约（由 tools 层归一化后传入）：
- vision:     image_path, prompt → {"description": str}
- asr:        resolved, is_url → {"text": str}
- tts:        text, voice, references, emotion, speed, pitch, language_boost → {"audio_bytes": bytes}
- voice_mgmt: op(clone/design/list/delete), ... → 各 op 结果 dict（design 可含 trial_audio_bytes）
- music:      op(generate/lyrics), ... → {"audio_bytes": bytes, ...} / 歌词 dict
- video:      op(generate/query/list/cancel), ... → {"video_url": str, ...} / 任务 dict
- image_gen:  prompt, image_size, num_inference_steps, n, reference_image → {"image_results": list}
- image_edit: image_path, prompt, num_inference_steps → {"image_results": list}
- rerank:     query, documents → {"results": list}
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from entities._sdk import ErrorCause

CAP_VISION = "vision"
CAP_ASR = "asr"
CAP_TTS = "tts"
CAP_VOICE_MGMT = "voice_mgmt"
CAP_MUSIC = "music"
CAP_VIDEO = "video"
CAP_IMAGE_GEN = "image_gen"
CAP_IMAGE_EDIT = "image_edit"
CAP_RERANK = "rerank"

ALL_CAPABILITIES = (
    CAP_VISION, CAP_ASR, CAP_TTS, CAP_VOICE_MGMT, CAP_MUSIC,
    CAP_VIDEO, CAP_IMAGE_GEN, CAP_IMAGE_EDIT, CAP_RERANK,
)


class ProviderUnavailable(Exception):
    """provider 未配置/不可用，路由器跳过并尝试下一 provider。"""


class CapabilityNotSupported(Exception):
    """provider 不支持该能力/操作，路由器跳过并尝试下一 provider。"""


class ModelChainError(RuntimeError):
    """模型链全部失败，携带逐模型错误明细（路由器聚合归因用）。"""

    def __init__(self, message: str, model_errors: Dict[str, str]) -> None:
        super().__init__(message)
        self.model_errors = model_errors


class MediaProvider:
    """媒体能力 provider 基类。子类覆盖 capabilities 与所需的 run 实现。"""

    name: str = ""
    capabilities: frozenset = frozenset()

    def is_configured(self, capability: str) -> bool:
        """该能力所需的凭据/模型是否就绪。未就绪时路由器跳过本 provider。"""
        return True

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        raise CapabilityNotSupported(f"provider '{self.name}' 不支持能力 '{capability}'")


def error_payload(
    message: str,
    *,
    cause: "ErrorCause | None" = None,
    hint: str = "",
    retryable: "bool | None" = None,
    **context: Any,
) -> Dict[str, Any]:
    """构造与 core.tool_errors.tool_error 同构的错误 dict（媒体路由内部 dict 流使用）。"""
    payload: Dict[str, Any] = {"error": message}
    if cause is not None:
        payload["cause"] = cause.value
    if hint:
        payload["hint"] = hint
    if retryable is not None:
        payload["retryable"] = retryable
    for key, value in context.items():
        if value is not None:
            payload[key] = value
    return payload


def classify_media_errors(errors: Dict[str, str]) -> Tuple[ErrorCause, bool, str]:
    """根据各 provider/模型错误详情推断整体归因，让 AI 拿到可决策的 cause/hint。"""
    detail = " ".join(errors.values()).lower()
    if any(k in detail for k in ("http 401", "http 403", "(1004)", "[1004]", "(2049)", "[2049]",
                                 "invalid api key", "unauthorized")):
        return (ErrorCause.CONFIG, False, "API Key 无效或无权限，请检查对应 provider 的密钥配置")
    if any(k in detail for k in ("http 402", "(1008)", "[1008]", "余额", "insufficient")):
        return (ErrorCause.CONFIG, False, "账户余额不足，请充值后重试")
    if any(k in detail for k in ("http 422", "(1026)", "[1026]", "(1027)", "[1027]", "敏感")):
        return (ErrorCause.PARAM, False, "内容触发平台敏感审核，请调整提示词/素材后重试")
    if "http 429" in detail:
        return (ErrorCause.NETWORK, True, "触发平台限流，可稍后重试")
    if "timeout" in detail or "超时" in detail:
        return (ErrorCause.TIMEOUT, True, "可稍后重试")
    return (ErrorCause.NETWORK, True, "可稍后重试，或在媒体库配置中调整该能力的 provider 优先级")
