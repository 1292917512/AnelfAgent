"""语音合成（TTS）与音色管理协议适配器：收口不同供应商的语音 API 差异。

同步合成、长文本异步合成（t2a_async）、音色复刻/设计/查询/删除均由适配器收口，
MediaClient 不感知具体协议差异。

扩展新供应商：实现 SpeechAdapter 并调用 register_speech_adapter() 注册；
供应商配置可通过 media_protocol 显式指定适配器，未指定时按 host 规则自动匹配。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from agent.llm.adapter_base import AdapterRequest, host_root


@dataclass(slots=True)
class SpeechParams:
    """一次语音合成的参数集合（适配器按需取用，不支持的字段忽略）。"""

    model: str
    text: str
    voice: str = ""
    response_format: str = "mp3"
    emotion: str = ""
    speed: Optional[float] = None
    vol: Optional[float] = None
    pitch: Optional[int] = None
    language_boost: str = ""
    references: Optional[List[Dict[str, str]]] = None


@dataclass(slots=True)
class TtsAsyncTaskState:
    """归一化的异步语音任务状态。"""

    status: str  # processing / succeeded / failed
    file_id: str = ""
    error: str = ""


class SpeechAdapter(ABC):
    """语音协议适配器基类。"""

    name: str = ""
    supports_async: bool = False
    supports_voice_mgmt: bool = False
    binary_response: bool = False  # True 表示合成接口直接返回音频字节流（非 JSON）

    @abstractmethod
    def build_tts_request(self, base_url: str, params: SpeechParams) -> AdapterRequest:
        """构建同步语音合成请求。"""

    def extract_audio(self, result: Dict[str, Any]) -> bytes:
        """从 JSON 合成响应提取音频字节（binary_response=True 的协议不经过此方法）。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持 JSON 音频解析")

    # ------------------------------------------------------------------
    # 长文本异步合成（可选）
    # ------------------------------------------------------------------

    def build_async_create_request(self, base_url: str, params: SpeechParams) -> AdapterRequest:
        """构建异步合成任务创建请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持异步合成")

    def extract_async_task_id(self, result: Dict[str, Any]) -> str:
        """从异步创建响应提取任务 ID。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持异步合成")

    def build_async_query_request(self, base_url: str, task_id: str) -> AdapterRequest:
        """构建异步任务查询请求。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持异步合成")

    def parse_async_query(self, result: Dict[str, Any]) -> TtsAsyncTaskState:
        """解析异步任务查询响应为归一化状态。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持异步合成")

    def build_retrieve_request(self, base_url: str, file_id: str) -> AdapterRequest:
        """构建文件下载地址获取请求（按 file_id 换取下载 URL 的协议使用）。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持文件检索")

    def extract_download_url(self, result: Dict[str, Any]) -> str:
        """从文件检索响应提取下载 URL。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持文件检索")

    # ------------------------------------------------------------------
    # 音色管理（可选）
    # ------------------------------------------------------------------

    def build_voice_clone_request(
        self, base_url: str, *, file_id: int, voice_id: str,
        preview_text: str = "", model: str = "",
        prompt_file_id: int = 0, prompt_text: str = "",
        need_noise_reduction: bool = False,
        need_volume_normalization: bool = False,
    ) -> AdapterRequest:
        """构建音色复刻请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色复刻")

    def parse_voice_clone(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析音色复刻响应。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色复刻")

    def build_voice_design_request(
        self, base_url: str, *, prompt: str, preview_text: str, voice_id: str = "",
    ) -> AdapterRequest:
        """构建音色设计请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色设计")

    def parse_voice_design(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析音色设计响应（含 voice_id 与试听音频字节）。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色设计")

    def build_get_voice_request(self, base_url: str, *, voice_type: str) -> AdapterRequest:
        """构建音色查询请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色查询")

    def parse_get_voice(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析音色查询响应。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色查询")

    def build_delete_voice_request(
        self, base_url: str, *, voice_type: str, voice_id: str,
    ) -> AdapterRequest:
        """构建音色删除请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色删除")

    def parse_delete_voice(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析音色删除响应。"""
        raise NotImplementedError(f"语音协议 '{self.name}' 不支持音色删除")


class OpenAISpeechAdapter(SpeechAdapter):
    """OpenAI 风格：POST {base_url}/audio/speech（SiliconFlow 等兼容服务）。"""

    name = "openai"
    binary_response = True

    def build_tts_request(self, base_url: str, params: SpeechParams) -> AdapterRequest:
        payload: Dict[str, Any] = {
            "model": params.model,
            "input": params.text,
            "response_format": params.response_format,
        }
        if params.references:
            payload["references"] = params.references
        else:
            voice = params.voice or (f"{params.model}:alex" if params.model else "alex")
            payload["voice"] = voice if ":" in voice else f"{params.model}:{voice}"
        return AdapterRequest(url=f"{base_url}/audio/speech", payload=payload)


class MiniMaxSpeechAdapter(SpeechAdapter):
    """MiniMax 语音协议：t2a_v2 同步合成 + t2a_async_v2 长文本异步 + 音色管理。

    接口挂在网关机根路径，始终从 host 根拼接。
    """

    name = "minimax"
    supports_async = True
    supports_voice_mgmt = True
    _DEFAULT_VOICE = "male-qn-qingse"

    @staticmethod
    def _check_base_resp(result: Dict[str, Any]) -> None:
        base_resp = result.get("base_resp") or {}
        code = base_resp.get("status_code", 0)
        if code != 0:
            raise RuntimeError(f"MiniMax API 错误 ({code}): {base_resp.get('status_msg', '')}")

    @staticmethod
    def _voice_setting(params: SpeechParams) -> Dict[str, Any]:
        setting: Dict[str, Any] = {"voice_id": params.voice or MiniMaxSpeechAdapter._DEFAULT_VOICE}
        if params.speed is not None:
            setting["speed"] = params.speed
        if params.vol is not None:
            setting["vol"] = params.vol
        if params.pitch is not None:
            setting["pitch"] = params.pitch
        if params.emotion:
            setting["emotion"] = params.emotion
        return setting

    def build_tts_request(self, base_url: str, params: SpeechParams) -> AdapterRequest:
        payload: Dict[str, Any] = {
            "model": params.model,
            "text": params.text,
            "stream": False,
            "voice_setting": self._voice_setting(params),
            "audio_setting": {"format": params.response_format or "mp3"},
            "output_format": "hex",
        }
        if params.language_boost:
            payload["language_boost"] = params.language_boost
        return AdapterRequest(url=f"{host_root(base_url)}/v1/t2a_v2", payload=payload)

    def extract_audio(self, result: Dict[str, Any]) -> bytes:
        self._check_base_resp(result)
        audio_hex = (result.get("data") or {}).get("audio", "")
        if not audio_hex:
            raise ValueError(f"语音合成响应中无音频数据: {result}")
        return bytes.fromhex(audio_hex)

    # ------------------------------------------------------------------
    # 长文本异步合成
    # ------------------------------------------------------------------

    def build_async_create_request(self, base_url: str, params: SpeechParams) -> AdapterRequest:
        payload: Dict[str, Any] = {
            "model": params.model,
            "text": params.text,
            "voice_setting": self._voice_setting(params),
            "audio_setting": {"format": params.response_format or "mp3"},
        }
        if params.language_boost:
            payload["language_boost"] = params.language_boost
        return AdapterRequest(url=f"{host_root(base_url)}/v1/t2a_async_v2", payload=payload)

    def extract_async_task_id(self, result: Dict[str, Any]) -> str:
        self._check_base_resp(result)
        return str(result.get("task_id", ""))

    def build_async_query_request(self, base_url: str, task_id: str) -> AdapterRequest:
        return AdapterRequest(
            url=f"{host_root(base_url)}/v1/query/t2a_async_query_v2",
            method="GET",
            params={"task_id": task_id},
        )

    def parse_async_query(self, result: Dict[str, Any]) -> TtsAsyncTaskState:
        self._check_base_resp(result)
        status = str(result.get("status", "")).lower()
        if status == "success":
            return TtsAsyncTaskState(status="succeeded", file_id=str(result.get("file_id", "")))
        if status in ("failed", "expired"):
            base_resp = result.get("base_resp") or {}
            return TtsAsyncTaskState(
                status="failed",
                error=base_resp.get("status_msg", "") or f"语音合成任务 {status}",
            )
        return TtsAsyncTaskState(status="processing")

    def build_retrieve_request(self, base_url: str, file_id: str) -> AdapterRequest:
        return AdapterRequest(
            url=f"{host_root(base_url)}/v1/files/retrieve",
            method="GET",
            params={"file_id": file_id},
        )

    def extract_download_url(self, result: Dict[str, Any]) -> str:
        self._check_base_resp(result)
        file_obj = result.get("file") or {}
        return file_obj.get("download_url", "")

    # ------------------------------------------------------------------
    # 音色管理
    # ------------------------------------------------------------------

    def build_voice_clone_request(
        self, base_url: str, *, file_id: int, voice_id: str,
        preview_text: str = "", model: str = "",
        prompt_file_id: int = 0, prompt_text: str = "",
        need_noise_reduction: bool = False,
        need_volume_normalization: bool = False,
    ) -> AdapterRequest:
        payload: Dict[str, Any] = {
            "file_id": file_id,
            "voice_id": voice_id,
            "need_noise_reduction": need_noise_reduction,
            "need_volume_normalization": need_volume_normalization,
        }
        if prompt_file_id and prompt_text:
            payload["clone_prompt"] = {
                "prompt_audio": prompt_file_id,
                "prompt_text": prompt_text,
            }
        if preview_text:
            payload["text"] = preview_text
            payload["model"] = model or "speech-2.8-hd"
        return AdapterRequest(url=f"{host_root(base_url)}/v1/voice_clone", payload=payload)

    def parse_voice_clone(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_base_resp(result)
        return {
            "demo_audio": result.get("demo_audio", ""),
            "input_sensitive": result.get("input_sensitive"),
        }

    def build_voice_design_request(
        self, base_url: str, *, prompt: str, preview_text: str, voice_id: str = "",
    ) -> AdapterRequest:
        payload: Dict[str, Any] = {"prompt": prompt, "preview_text": preview_text}
        if voice_id:
            payload["voice_id"] = voice_id
        return AdapterRequest(url=f"{host_root(base_url)}/v1/voice_design", payload=payload)

    def parse_voice_design(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_base_resp(result)
        trial_hex = result.get("trial_audio", "")
        return {
            "voice_id": result.get("voice_id", ""),
            "trial_audio": bytes.fromhex(trial_hex) if trial_hex else b"",
        }

    def build_get_voice_request(self, base_url: str, *, voice_type: str) -> AdapterRequest:
        return AdapterRequest(
            url=f"{host_root(base_url)}/v1/get_voice",
            payload={"voice_type": voice_type or "all"},
        )

    def parse_get_voice(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_base_resp(result)
        return {
            "system_voice": result.get("system_voice", []),
            "voice_cloning": result.get("voice_cloning", []),
            "voice_generation": result.get("voice_generation", []),
        }

    def build_delete_voice_request(
        self, base_url: str, *, voice_type: str, voice_id: str,
    ) -> AdapterRequest:
        return AdapterRequest(
            url=f"{host_root(base_url)}/v1/delete_voice",
            payload={"voice_type": voice_type, "voice_id": voice_id},
        )

    def parse_delete_voice(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_base_resp(result)
        return {
            "voice_id": result.get("voice_id", ""),
            "created_time": result.get("created_time", ""),
        }


_ADAPTERS: Dict[str, SpeechAdapter] = {}
_HOST_RULES: List[Tuple[str, str]] = []
_default_adapter: str = ""


def register_speech_adapter(
    adapter: SpeechAdapter,
    *,
    host_keywords: Tuple[str, ...] = (),
    default: bool = False,
) -> None:
    """注册语音协议适配器。

    host_keywords: base_url 主机名包含任一关键字时自动匹配该适配器；
    default: 未命中任何规则时的兜底适配器。
    """
    global _default_adapter
    _ADAPTERS[adapter.name] = adapter
    for keyword in host_keywords:
        _HOST_RULES.append((keyword, adapter.name))
    if default or not _default_adapter:
        _default_adapter = adapter.name


def resolve_speech_adapter(base_url: str, protocol: str = "") -> SpeechAdapter:
    """解析语音协议适配器：显式 protocol 优先，其次 host 规则，最后兜底。

    media_protocol 字段为各媒体协议共用，protocol 不属于语音协议时
    不视为错误，回退 host 规则自动匹配。
    """
    if protocol:
        adapter = _ADAPTERS.get(protocol)
        if adapter is not None:
            return adapter
        from core.log import log
        log(f"media_protocol '{protocol}' 不是语音协议，按 host 规则自动匹配", "DEBUG", tag="媒体")
    host = urlparse(base_url).netloc
    for keyword, name in _HOST_RULES:
        if keyword in host:
            return _ADAPTERS[name]
    return _ADAPTERS[_default_adapter]


register_speech_adapter(OpenAISpeechAdapter(), default=True)
register_speech_adapter(MiniMaxSpeechAdapter(), host_keywords=("minimaxi.com", "minimax.io"))
