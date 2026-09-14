"""原生实时语音客户端协议 — 提供方直连音频通道的统一抽象。

cascade 之外的第二种实时形态：提供方云端直接吃麦克风流、直接产回复
音频流（语音到语音一跳直达，延迟最低；人格/记忆不经思维链路，由
提供方的会话指令注入）。核心层只定义客户端协议与事件模型，双方言
（openai.py / gemini.py）各自实现协议细节。

事件模型（NativeEvent）：
- audio：回复音频块（PCM16，sample_rate 随块声明）；
- transcript：转写文本（role=user/assistant，final 区分定稿）；
- speech_started：提供方检测到用户开口（barge-in 信号，引擎据此打断）；
- turn_complete：一轮回复结束；
- error：通道错误（引擎降级/重连判定用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass(slots=True)
class NativeEvent:
    """原生实时通道事件。"""

    kind: str
    """audio | transcript | speech_started | turn_complete | error"""
    pcm: bytes = b""
    sample_rate: int = 24000
    role: str = ""
    text: str = ""
    final: bool = False
    message: str = ""


@runtime_checkable
class NativeRealtimeClient(Protocol):
    """原生实时语音客户端接口（一次会话一个实例）。"""

    input_rate: int
    """客户端期望的麦克风 PCM16 采样率（引擎据此重采样喂入）。"""

    async def connect(self) -> None:
        """建立通道并完成会话初始化（模型/音色/转写配置）。"""
        ...

    async def send_audio(self, pcm: bytes) -> None:
        """喂入一块麦克风 PCM16（采样率须为 input_rate）。"""
        ...

    def events(self) -> AsyncIterator[NativeEvent]:
        """通道事件流（连接存活期间持续产出）。"""
        ...

    async def interrupt(self) -> None:
        """截断当前回复（barge-in：停止当前音频产出并清空输入缓冲）。"""
        ...

    async def close(self) -> None:
        """关闭通道（幂等）。"""
        ...


def provider_credentials(*, openai: bool) -> tuple[str, str]:
    """从模型供应商配置取实时通道凭据 (api_key, base_url)。

    openai=True 取 OpenAI 凭据（OpenAI Realtime），否则取 Gemini 凭据
    （Gemini Live）；无匹配供应商返回 ("", "")（调用方判定不可用）。
    """
    from agent.llm import get_llm_manager
    manager = get_llm_manager()
    want = ("openai", "api.openai.com") if openai else ("gemini", "googleapis")
    for provider in manager.list_providers():
        api_type = str(getattr(provider, "api_type", "") or "").lower()
        base_url = str(getattr(provider, "base_url", "") or "").lower()
        if any(k in api_type or k in base_url for k in want):
            api_key = str(getattr(provider, "api_key", "") or "")
            base = str(getattr(provider, "base_url", "") or "")
            if api_key:
                return api_key, base
    return "", ""
