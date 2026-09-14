"""流式 ASR — 边说边转的增量转写接口与事件模型。

与整段 ASR（providers.py 的 KIND_ASR）互补：实时对话需要"语音还在进行
就拿到部分文本"的低延迟转写。核心层只定义会话接口与事件，具体实现
（FunASR 滚动窗、云端流式 ASR 等）以组件形式注册——提供者协议复用
providers.py 的注册表（kind=KIND_ASR_STREAM，独立优先级链）。

事件模型：
- partial：部分转写（随语音推进不断更新，仅用于展示/预判，不驱动回复）；
- final：一次端点收束的定稿转写（驱动回复的输入）。

会话生命周期：open_session() → accept_pcm() 逐帧喂入 → close() 收尾
（未收束的缓冲以 final 事件吐出）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol, runtime_checkable

KIND_ASR_STREAM = "asr_stream"


@dataclass(slots=True)
class AsrEvent:
    """一次转写事件（partial 或 final）。"""

    kind: str
    """partial | final。"""
    text: str = ""
    segments: List[Dict[str, Any]] = field(default_factory=list)
    """final 事件的结构化分段（含时间戳/声纹向量，入库管线直接可用）。"""


@runtime_checkable
class StreamingAsrSession(Protocol):
    """一次流式转写会话（一次连续语音一个实例）。"""

    async def accept_pcm(self, pcm: bytes, sample_rate: int) -> List[AsrEvent]:
        """喂入一帧 PCM16，返回本帧产出的事件（可为空列表）。"""
        ...

    async def close(self) -> List[AsrEvent]:
        """会话收尾：未收束缓冲以 final 事件吐出。"""
        ...


@runtime_checkable
class StreamingAsrProvider(Protocol):
    """流式 ASR 提供者接口（注册进 providers.py 的 asr_stream 链）。"""

    name: str
    kind: str  # KIND_ASR_STREAM
    priority: int

    async def check_available(self) -> bool:
        ...

    def open_session(self, sample_rate: int = 16000) -> StreamingAsrSession:
        """开启一次流式转写会话。"""
        ...
