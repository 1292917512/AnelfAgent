"""音频能力提供者注册表 — ASR 转写 / 声纹提取的接口抽象与组件注册。

核心层只定义接口与解析顺序，具体实现（FunASR、云端 ASR 等）以组件形式
从实体/插件注册进来（经 entities._sdk.register_audio_provider 桥接）。

组件契约：
- kind：``asr``（语音转写，产出分段文本+时间戳）、``voiceprint``
  （声纹向量提取）或 ``asr_stream``（流式转写会话，边说边转，
  会话接口见 streaming.py）；
- priority：解析顺序（小值优先），同 kind 内首个 ``check_available()``
  通过的提供者承担调用——回退链由优先级链天然构成；
- 配置自管：提供者自行读取自己的配置键（配置中心可见），
  ``check_available`` 即"配置/服务就绪"判定。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from core.log import log

# 提供者类别（注册表键）
KIND_ASR = "asr"
KIND_VOICEPRINT = "voiceprint"
KIND_ASR_STREAM = "asr_stream"
KINDS = (KIND_ASR, KIND_VOICEPRINT, KIND_ASR_STREAM)


@runtime_checkable
class AudioAsrProvider(Protocol):
    """语音转写提供者接口。"""

    name: str
    kind: str  # KIND_ASR
    priority: int

    async def check_available(self) -> bool:
        """配置/服务是否就绪（不可用时解析跳过该提供者）。"""
        ...

    async def transcribe(
        self, audio_path: str, source_time: str = "",
    ) -> List[Dict[str, Any]]:
        """转写音频为分段结果。

        Returns:
            [{"start_ms": int, "end_ms": int, "text": str,
              "vector": list[float] | None,
              "abs_start_ms": int | None, "abs_end_ms": int | None}]
        """
        ...


@runtime_checkable
class AudioVoiceprintProvider(Protocol):
    """声纹向量提取提供者接口。"""

    name: str
    kind: str  # KIND_VOICEPRINT
    priority: int

    async def check_available(self) -> bool:
        ...

    async def embed(self, audio_path: str) -> Optional[List[float]]:
        """提取音频的声纹向量（失败/不可用返回 None）。"""
        ...


class AudioProviderRegistry:
    """音频提供者注册表（按 kind 分组、priority 升序）。"""

    def __init__(self) -> None:
        self._providers: Dict[str, List[Any]] = {k: [] for k in KINDS}

    def register(self, provider: Any) -> None:
        kind = getattr(provider, "kind", "")
        name = getattr(provider, "name", "")
        if kind not in KINDS:
            raise ValueError(f"非法音频提供者类别: {kind!r}（可选 {KINDS}）")
        if not name:
            raise ValueError("音频提供者缺少 name")
        entries = self._providers[kind]
        entries[:] = [p for p in entries if p.name != name]  # 同名覆盖
        entries.append(provider)
        entries.sort(key=lambda p: p.priority)
        log(f"音频提供者已注册: {kind}/{name} (priority={provider.priority})",
            "DEBUG", tag="音频")

    def unregister(self, kind: str, name: str) -> None:
        if kind in self._providers:
            self._providers[kind][:] = [p for p in self._providers[kind] if p.name != name]

    def list(self, kind: Optional[str] = None) -> List[Any]:
        if kind:
            return list(self._providers.get(kind, []))
        return [p for entries in self._providers.values() for p in entries]

    async def resolve(self, kind: str) -> Optional[Any]:
        """解析该类别首个可用的提供者（优先级链回退）。"""
        for provider in self._providers.get(kind, []):
            try:
                if await provider.check_available():
                    return provider
            except Exception:
                continue
        return None

    def reset(self) -> None:
        """清空注册表（测试用）。"""
        for entries in self._providers.values():
            entries.clear()


_registry: Optional[AudioProviderRegistry] = None


def get_audio_registry() -> AudioProviderRegistry:
    """进程内单例。"""
    global _registry
    if _registry is None:
        _registry = AudioProviderRegistry()
    return _registry
