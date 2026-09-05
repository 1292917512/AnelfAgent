"""微信频道配置 — 通过腾讯 iLink Bot API 接入个人微信。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/weixin，键 weixin_<field>），值统一存 ConfigManager。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent.channel.base import ChannelConfig

from .ilink_client import ILINK_BASE_URL, WEIXIN_CDN_BASE_URL

_PASSWORD = {"value_type": "password"}
_TEXT = {"value_type": "text"}
_URL = {"value_type": "url"}
_ADVANCED = {"advanced": True}


class WeixinConfig(ChannelConfig):
    """微信频道配置（iLink Bot API）。"""

    enabled: bool = Field(default=False, description="是否启用微信频道")
    account_id: str = Field(
        default="", description="iLink Bot 账号 ID（扫码登录后获得，形如 ...@im.bot）")
    token: str = Field(
        default="", description="iLink Bot Token（扫码登录后自动保存）",
        json_schema_extra=_PASSWORD)
    base_url: str = Field(
        default=ILINK_BASE_URL, description="iLink API 地址",
        json_schema_extra=_URL)
    cdn_base_url: str = Field(
        default=WEIXIN_CDN_BASE_URL, description="微信 CDN 地址（媒体上传/下载）",
        json_schema_extra=_URL)
    dm_policy: Literal["open", "allowlist", "disabled"] = Field(
        default="open", description="私聊访问策略（open=所有人 / allowlist=白名单 / disabled=禁用）")
    allow_from: str = Field(
        default="", description="私聊白名单（用户 ID，多个用英文逗号分隔，dm_policy=allowlist 时生效）",
        json_schema_extra=_TEXT)
    group_policy: Literal["open", "allowlist", "disabled"] = Field(
        default="disabled",
        description="群聊访问策略（注意：iLink bot 身份通常无法接收普通群消息，限制在 iLink 侧）")
    group_allow_from: str = Field(
        default="", description="群聊白名单（群聊 ID，多个用英文逗号分隔，group_policy=allowlist 时生效）",
        json_schema_extra=_TEXT)
    split_multiline_messages: bool = Field(
        default=False, description="多行消息逐行拆分发送（默认 compact 模式：能放下就整条发）")
    typing_indicator: bool = Field(default=True, description="处理消息时向对方显示「正在输入」状态")

    # ---- 发送与限频调参（高级项，UI 折叠） ----
    send_chunk_delay_seconds: float = Field(
        default=1.5, description="文本分块发送间隔（秒）", json_schema_extra=_ADVANCED)
    send_chunk_retries: int = Field(
        default=4, description="单块发送重试次数", json_schema_extra=_ADVANCED)
    send_chunk_retry_delay_seconds: float = Field(
        default=1.0, description="发送重试基础退避（秒）", json_schema_extra=_ADVANCED)
    rate_limit_circuit_threshold: int = Field(
        default=1, description="限频熔断触发次数", json_schema_extra=_ADVANCED)
    rate_limit_circuit_window_seconds: float = Field(
        default=30.0, description="限频统计窗口（秒）", json_schema_extra=_ADVANCED)
    rate_limit_circuit_open_seconds: float = Field(
        default=30.0, description="熔断断开时长（秒）", json_schema_extra=_ADVANCED)
    text_batch_delay_seconds: float = Field(
        default=3.0, description="文本合批静默期（秒）", json_schema_extra=_ADVANCED)
    text_batch_split_delay_seconds: float = Field(
        default=5.0, description="长片段合批静默期（秒）", json_schema_extra=_ADVANCED)


CONFIG_MODEL = WeixinConfig
