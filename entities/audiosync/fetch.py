"""音源取回器注册 — 核心回听/分析定位原始音频时的来源下载通道。

把当前生效的同步来源（OpenList 远程下载等）注册为核心音频层的
音源取回器：核心回听（listen）沿录制合并清单取回源文件时，
经该通道下载远程文件；本地文件由核心兜底直读。
"""

from __future__ import annotations

from entities._sdk import register_audio_source_fetcher


async def _fetch(source_path: str) -> tuple[str, bool]:
    """经当前生效的同步来源取回源文件（未配置来源时抛错让链继续）。"""
    from .framework import active_source
    source = active_source()
    if source is None:
        raise FileNotFoundError("未配置音源同步来源")
    return await source.fetch(source_path)


register_audio_source_fetcher("audiosync", _fetch, priority=10)
