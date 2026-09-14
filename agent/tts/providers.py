"""TTS 提供者注册表 — 流式语音合成的接口抽象与组件注册。

核心层只定义接口与解析顺序，具体实现（MiniMax、OpenAI 风格、edge-tts 等）
以组件形式从实体经 entities._sdk.register_tts_provider 注册。

组件契约：
- name / priority：解析顺序（小值优先），首个 ``check_available()`` 通过的
  提供者承担调用——回退链由优先级链天然构成；
- ``stream_synthesize``：文本 → PCM16 字节块异步流（低延迟管线逐块消费，
  不做整段缓冲）；产出的实际采样率经返回流的 ``sample_rate`` 属性声明
  （播放链负责重采样，提供者不必自行对齐）；
- 配置自管：提供者自行读取自己的配置键，``check_available`` 即
  "配置/服务就绪"判定。

运行时失败降级：句级管线（pipeline.py）在某个提供者流中途失败时，
按优先级链取下一个提供者重试当前句——首字节前失败可无缝切换，
首字节后失败截断该句并继续后续句（听感一跳优于整段失声）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, List, Optional, Protocol, runtime_checkable

from core.log import log

_LOG_TAG = "语音合成"


class TtsStream:
    """一句文本的合成结果流（PCM16 字节块 + 采样率声明）。"""

    def __init__(self, chunks: AsyncIterator[bytes], sample_rate: int) -> None:
        self.chunks = chunks
        self.sample_rate = sample_rate


@runtime_checkable
class TtsProvider(Protocol):
    """流式 TTS 提供者接口。"""

    name: str
    priority: int

    async def check_available(self) -> bool:
        """配置/服务是否就绪（不可用时解析跳过该提供者）。"""
        ...

    def stream_synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        sample_rate: int = 24000,
    ) -> TtsStream:
        """合成一句文本为 PCM16 字节块流（实现内不得做整段缓冲）。"""
        ...


class TtsProviderRegistry:
    """TTS 提供者注册表（priority 升序解析链）。"""

    def __init__(self) -> None:
        self._providers: List[Any] = []

    def register(self, provider: Any) -> None:
        name = getattr(provider, "name", "")
        if not name:
            raise ValueError("TTS 提供者缺少 name")
        self._providers[:] = [p for p in self._providers if p.name != name]  # 同名覆盖
        self._providers.append(provider)
        self._providers.sort(key=lambda p: p.priority)
        log(f"TTS 提供者已注册: {name} (priority={provider.priority})",
            "DEBUG", tag=_LOG_TAG)

    def unregister(self, name: str) -> None:
        self._providers[:] = [p for p in self._providers if p.name != name]

    def list(self) -> List[Any]:
        return list(self._providers)

    async def resolve(self) -> Optional[Any]:
        """解析首个可用的提供者。"""
        for provider in self._providers:
            try:
                if await provider.check_available():
                    return provider
            except Exception:
                continue
        return None

    async def available_chain(self) -> List[Any]:
        """按优先级列出当前全部可用提供者（句级失败降级的重试链）。"""
        chain: List[Any] = []
        for provider in self._providers:
            try:
                if await provider.check_available():
                    chain.append(provider)
            except Exception:
                continue
        return chain

    def reset(self) -> None:
        """清空注册表（测试用）。"""
        self._providers.clear()


_registry: Optional[TtsProviderRegistry] = None


def get_tts_registry() -> TtsProviderRegistry:
    """进程内单例。"""
    global _registry
    if _registry is None:
        _registry = TtsProviderRegistry()
    return _registry


async def synthesize_sentence(
    text: str,
    *,
    voice: str = "",
    sample_rate: int = 24000,
    chain: Optional[List[Any]] = None,
) -> Optional[TtsStream]:
    """合成一句：优先级链 + 首字节前失败无缝降级。

    返回的流在首个字节产出前若提供者异常，自动换下一个提供者重试；
    全部提供者失败返回 None（调用方截断该句继续后续句）。
    """
    providers = chain if chain is not None else await get_tts_registry().available_chain()

    async def _first_chunk_checked(provider: Any) -> Optional[TtsStream]:
        """包装流：首块产出失败（提供者级错误）返回 None 触发降级。"""
        stream = provider.stream_synthesize(text, voice=voice, sample_rate=sample_rate)
        iterator = stream.chunks.__aiter__()
        try:
            first = await iterator.__anext__()
        except StopAsyncIteration:
            return None
        except Exception as exc:
            log(f"TTS 提供者 {provider.name} 首块失败（降级重试）: {exc}",
                "DEBUG", tag=_LOG_TAG)
            return None

        async def _rest() -> AsyncIterator[bytes]:
            yield first
            async for chunk in iterator:
                yield chunk

        return TtsStream(_rest(), stream.sample_rate)

    for provider in providers:
        stream = await _first_chunk_checked(provider)
        if stream is not None:
            return stream
    return None
