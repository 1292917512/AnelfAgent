"""媒体库实体：统一媒体工具面 + provider 路由 + 专属配置面板。

配置单一数据源为 entities/media/config.json（经 /api/entity/media/config 读写），
不注册到 ConfigManager，避免双写不一致。
"""

from __future__ import annotations

from entities._sdk import entity, entity_manifest

entity("media", "多模态媒体库 - 图片识别、语音转文字、文字转语音、音色管理、音乐生成、图片生成/编辑、视频生成、文档重排序（统一接口 + 可配置 provider 优先级）")

entity_manifest(
    display_name="媒体库",
    icon="image",
    description="统一媒体工具面：图片识别/生成/编辑、语音合成/识别、音色管理、音乐、视频、重排序；provider 优先级可配置",
    version="2.0.0",
    order=25,
    group="media",
)

from . import tools  # noqa: E402,F401  触发 @tool 注册
