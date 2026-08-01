"""音乐生成协议适配器：收口音乐生成 / 歌词生成 / 翻唱预处理的 API 差异。

目前仅 MiniMax 提供音乐生成能力，registry 无默认兜底：
resolve_music_adapter 未命中时抛出异常，由上层转换为"供应商不支持"的工具错误。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from agent.llm.adapter_base import AdapterRequest, host_root


@dataclass(slots=True)
class MusicParams:
    """一次音乐生成的参数集合。"""

    model: str
    prompt: str = ""
    lyrics: str = ""
    is_instrumental: bool = False
    audio_url: str = ""
    audio_base64: str = ""
    cover_feature_id: str = ""


@dataclass(slots=True)
class MusicResult:
    """音乐生成结果（音频字节 + 元信息）。"""

    audio: bytes
    extra_info: Dict[str, Any] = field(default_factory=dict)


class MusicAdapter(ABC):
    """音乐协议适配器基类。"""

    name: str = ""

    @abstractmethod
    def build_music_request(self, base_url: str, params: MusicParams) -> AdapterRequest:
        """构建音乐生成请求。"""

    @abstractmethod
    def extract_music(self, result: Dict[str, Any]) -> MusicResult:
        """从音乐生成响应提取音频字节与元信息。"""

    def build_lyrics_request(
        self, base_url: str, *, mode: str, prompt: str = "", lyrics: str = "", title: str = "",
    ) -> AdapterRequest:
        """构建歌词生成请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"音乐协议 '{self.name}' 不支持歌词生成")

    def parse_lyrics(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析歌词生成响应。"""
        raise NotImplementedError(f"音乐协议 '{self.name}' 不支持歌词生成")

    def build_cover_preprocess_request(
        self, base_url: str, *, audio_url: str = "", audio_base64: str = "",
    ) -> AdapterRequest:
        """构建翻唱预处理请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"音乐协议 '{self.name}' 不支持翻唱预处理")

    def parse_cover_preprocess(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析翻唱预处理响应。"""
        raise NotImplementedError(f"音乐协议 '{self.name}' 不支持翻唱预处理")


class MiniMaxMusicAdapter(MusicAdapter):
    """MiniMax 音乐协议：music_generation / lyrics_generation / music_cover_preprocess。

    接口挂在网关机根路径，始终从 host 根拼接。
    """

    name = "minimax"

    @staticmethod
    def _check_base_resp(result: Dict[str, Any]) -> None:
        base_resp = result.get("base_resp") or {}
        code = base_resp.get("status_code", 0)
        if code != 0:
            raise RuntimeError(f"MiniMax API 错误 ({code}): {base_resp.get('status_msg', '')}")

    def build_music_request(self, base_url: str, params: MusicParams) -> AdapterRequest:
        payload: Dict[str, Any] = {
            "model": params.model,
            "stream": False,
            "output_format": "hex",
        }
        if params.prompt:
            payload["prompt"] = params.prompt
        if params.lyrics:
            payload["lyrics"] = params.lyrics
        if params.is_instrumental:
            payload["is_instrumental"] = True
        if params.cover_feature_id:
            payload["cover_feature_id"] = params.cover_feature_id
        elif params.audio_url:
            payload["audio_url"] = params.audio_url
        elif params.audio_base64:
            payload["audio_base64"] = params.audio_base64
        return AdapterRequest(url=f"{host_root(base_url)}/v1/music_generation", payload=payload)

    def extract_music(self, result: Dict[str, Any]) -> MusicResult:
        self._check_base_resp(result)
        audio_hex = (result.get("data") or {}).get("audio", "")
        if not audio_hex:
            raise ValueError(f"音乐生成响应中无音频数据: {result}")
        return MusicResult(
            audio=bytes.fromhex(audio_hex),
            extra_info=result.get("extra_info") or {},
        )

    def build_lyrics_request(
        self, base_url: str, *, mode: str, prompt: str = "", lyrics: str = "", title: str = "",
    ) -> AdapterRequest:
        payload: Dict[str, Any] = {"mode": mode or "write_full_song"}
        if prompt:
            payload["prompt"] = prompt
        if lyrics:
            payload["lyrics"] = lyrics
        if title:
            payload["title"] = title
        return AdapterRequest(url=f"{host_root(base_url)}/v1/lyrics_generation", payload=payload)

    def parse_lyrics(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_base_resp(result)
        return {
            "song_title": result.get("song_title", ""),
            "style_tags": result.get("style_tags", ""),
            "lyrics": result.get("lyrics", ""),
        }

    def build_cover_preprocess_request(
        self, base_url: str, *, audio_url: str = "", audio_base64: str = "",
    ) -> AdapterRequest:
        payload: Dict[str, Any] = {"model": "music-cover"}
        if audio_url:
            payload["audio_url"] = audio_url
        elif audio_base64:
            payload["audio_base64"] = audio_base64
        else:
            raise ValueError("翻唱预处理必须提供 audio_url 或 audio_base64 之一")
        return AdapterRequest(url=f"{host_root(base_url)}/v1/music_cover_preprocess", payload=payload)

    def parse_cover_preprocess(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_base_resp(result)
        return {
            "cover_feature_id": result.get("cover_feature_id", ""),
            "formatted_lyrics": result.get("formatted_lyrics", ""),
            "structure_result": result.get("structure_result", ""),
            "audio_duration": result.get("audio_duration", 0),
        }


_ADAPTERS: Dict[str, MusicAdapter] = {}
_HOST_RULES: List[Tuple[str, str]] = []


def register_music_adapter(
    adapter: MusicAdapter,
    *,
    host_keywords: Tuple[str, ...] = (),
) -> None:
    """注册音乐协议适配器（无默认兜底：音乐能力并非所有供应商都提供）。"""
    _ADAPTERS[adapter.name] = adapter
    for keyword in host_keywords:
        _HOST_RULES.append((keyword, adapter.name))


def resolve_music_adapter(base_url: str, protocol: str = "") -> MusicAdapter:
    """解析音乐协议适配器：显式 protocol 优先，其次 host 规则，不支持则抛异常。"""
    if protocol:
        adapter = _ADAPTERS.get(protocol)
        if adapter is not None:
            return adapter
    else:
        host = urlparse(base_url).netloc
        for keyword, name in _HOST_RULES:
            if keyword in host:
                return _ADAPTERS[name]
    raise NotImplementedError("当前音乐模型供应商不支持音乐生成协议（仅 MiniMax 支持）")


register_music_adapter(MiniMaxMusicAdapter(), host_keywords=("minimaxi.com", "minimax.io"))
