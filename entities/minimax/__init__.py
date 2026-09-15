"""MiniMax 实体 — MiniMax 平台能力接入核心路由的组件包。

把 MiniMax 直连客户端（client.py）封装为四类组件注册进核心：
- 视觉能力组件（Coding Plan 图片理解 + 平台图像生成）→ 视觉能力路由
- 声音能力组件（一次性语音合成 + 音色管理）→ 声音能力路由
- 检索组件（Coding Plan 联网检索）→ 检索提供者矩阵
- 流式 TTS 组件（HTTP / WebSocket 双传输）→ 核心 TTS 注册表

凭据经组件凭据中心（provider_keys.json，Web/AI/文件三面可配）；
非凭据参数自管于本目录 config.json（默认音色/模型/代理），
删除本目录即整体拔出（各核心路由的注册随之消失）。
"""

from entities._sdk import (
    entity,
    entity_manifest,
    register_retrieval_provider,
    register_sound_provider,
    register_tts_provider,
    register_visual_provider,
)

entity("minimax", "MiniMax - 图片理解/生成、语音合成/音色管理、联网检索、流式 TTS 的平台组件包")

entity_manifest(
    display_name="MiniMax",
    icon="sparkles",
    description="MiniMax 平台组件：视觉理解/图像生成、语音合成/音色管理、联网检索、流式 TTS（HTTP/WS 双传输）",
    version="2.0.0",
    order=31,
    group="minimax",
)

from .providers import (  # noqa: E402
    MiniMaxSearchProvider,
    MiniMaxSoundProvider,
    MiniMaxTtsProvider,
    MiniMaxVisualProvider,
)
from .ws_tts import MiniMaxWsTtsProvider  # noqa: E402


def register_components() -> None:
    """向各核心路由注册 MiniMax 组件（实体导入时调用；同名覆盖幂等）。"""
    register_visual_provider(MiniMaxVisualProvider())
    register_sound_provider(MiniMaxSoundProvider())
    register_retrieval_provider(MiniMaxSearchProvider())
    register_tts_provider(MiniMaxTtsProvider())
    register_tts_provider(MiniMaxWsTtsProvider())


register_components()

# 组件凭据登记（域页签的组件凭据面板与 AI 工具据此展示）
from entities._sdk import register_provider_key  # noqa: E402

register_provider_key(
    "minimax", domain="sound", title="MiniMax 平台",
    description="语音合成/音色复刻/图片生成的平台按量 Key",
)
register_provider_key(
    "minimax_coding_plan", domain="sound", title="MiniMax Coding Plan",
    description="Token Plan 订阅 Key（图片理解/联网检索走订阅配额）",
    extra_fields=[{"key": "api_host", "label": "订阅接入点",
                   "default": "", "secret": False}],
)
