"""原生实时语音三方言 — OpenAI Realtime / Gemini Live / 千问实时语音客户端。"""

from typing import Any

from agent.realtime.native.base import (
    NativeEvent,
    NativeRealtimeClient,
    provider_credentials,
)
from agent.realtime.native.gemini import GeminiLiveClient
from agent.realtime.native.openai import OpenAiRealtimeClient
from agent.realtime.native.qwen import QwenRealtimeClient

__all__ = [
    "GeminiLiveClient",
    "NativeEvent",
    "NativeRealtimeClient",
    "OpenAiRealtimeClient",
    "QwenRealtimeClient",
    "create_native_client",
    "provider_credentials",
]


def create_native_client(provider: str, *, voice: str = "", instructions: str = "") -> Any:
    """按配置创建原生实时客户端（openai | gemini | qwen）。"""
    provider = (provider or "openai").strip().lower()
    if provider == "gemini":
        return GeminiLiveClient(voice=voice or "Aoede", instructions=instructions)
    if provider == "qwen":
        return QwenRealtimeClient(voice=voice or "longanqian", instructions=instructions)
    return OpenAiRealtimeClient(voice=voice or "alloy", instructions=instructions)
