"""wcauto 频道配置 — 远程桥模式对接 Windows 侧微信个人号。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/wcauto，键 wcauto_<field>），值统一存 ConfigManager，
Web 配置中心与 AI 配置工具同源读写，写入即热更。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from agent.channel.base import ChannelConfig

_PASSWORD = {"value_type": "password"}
_URL = {"value_type": "url"}


class WcautoConfig(ChannelConfig):
    """wcauto 频道配置（微信个人号，经 Windows 桥远程对接）。"""

    enabled: bool = Field(default=False, description="是否启用 wcauto 频道")

    # ---- 桥连接 ----
    ws_url: str = Field(
        default="ws://127.0.0.1:8655/wcauto/ws",
        description="Windows 桥 WS 地址（桥部署见频道 docs/deployment-windows.md）",
        json_schema_extra=_URL)
    access_token: str = Field(
        default="", description="访问令牌（与桥 wcauto_bridge.json 的 token 一致）",
        json_schema_extra=_PASSWORD)
    reconnect_interval: int = Field(default=5, description="断线重连间隔（秒）")
    max_reconnect_attempts: int = Field(
        default=0, description="最大重连次数（0 = 无限重试）")
    rpc_timeout: float = Field(
        default=90.0, description="RPC 超时（秒；发送含 GUI 操作与回读校验，可能较慢）")

    # ---- 访问策略 ----
    dm_policy: Literal["open", "allowlist", "disabled"] = Field(
        default="open", description="私聊策略")
    dm_allow_from: str = Field(
        default="", description="私聊白名单（逗号分隔 wxid 或昵称）")
    group_policy: Literal["open", "allowlist", "disabled"] = Field(
        default="open", description="群聊策略")
    group_allow_from: str = Field(
        default="", description="群聊白名单（逗号分隔群 wxid 或群名）")
    require_mention: bool = Field(
        default=True,
        description="群聊中是否需要 @ 我才激活思考（私聊不受影响，所有消息仍会记录到对话历史）")
    self_names: str = Field(
        default="",
        description="额外的「我」的称呼（逗号分隔；群文本中出现 @这些名字 视为@我，"
                    "桥上报的微信昵称自动包含）")
    ignore_chats: str = Field(
        default="", description="忽略会话（逗号分隔 wxid 或昵称；这些会话消息完全丢弃）")

    # ---- 发送 ----
    send_verify: bool = Field(
        default=True, description="发送后回读数据库校验（更可靠，略慢）")
    text_chunk_length: int = Field(
        default=1500, description="长文本分段长度（微信单条不宜过长）")

    # ---- 入站媒体 ----
    auto_download_image: bool = Field(
        default=True, description="私聊/@我 时自动下载图片到 workspace/uploads/")
    auto_download_voice: bool = Field(
        default=True, description="私聊/@我 时自动下载语音（SILK 原始格式）")
    auto_download_file: bool = Field(
        default=False, description="私聊/@我 时自动下载文件附件")
    auto_download_video: bool = Field(
        default=False, description="私聊/@我 时自动下载视频")

    # ---- 差异化能力 ----
    recall_notify: bool = Field(
        default=True, description="检测到对方撤回时把原文作为系统消息写入会话")
    allow_write: bool = Field(
        default=False,
        description="开放敏感写工具（撤回/at群成员/转发/朋友圈互动/标签群发），"
                    "全部经审批门控；微信风控敏感，建议按需开启")

    @field_validator("dm_policy", "group_policy", mode="before")
    @classmethod
    def _normalize_policy(cls, v: object) -> object:
        """策略枚举大小写归一（Web/手工编辑容错）。"""
        return v.lower() if isinstance(v, str) else v


CONFIG_MODEL = WcautoConfig
