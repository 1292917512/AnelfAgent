"""QQ 频道配置 — 通过 OneBot v11 协议对接 NapCat / Lagrange 等。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/qq，键 qq_<field>），值统一存 ConfigManager（app_config.json），
Web 配置中心与 AI 配置工具同源读写，写入即热更。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent.channel.base import ChannelConfig

_PASSWORD = {"value_type": "password"}
_TEXT = {"value_type": "text"}
_URL = {"value_type": "url"}


class QQConfig(ChannelConfig):
    """QQ 频道配置（OneBot v11）。"""

    enabled: bool = Field(default=False, description="是否启用 QQ 频道")
    ws_mode: Literal["forward", "reverse"] = Field(
        default="reverse", description="连接模式")
    ws_url: str = Field(
        default="ws://127.0.0.1:3001", description="NapCat WS 地址（如 ws://127.0.0.1:3001）",
        json_schema_extra={"tag": "forward"})
    reconnect_interval: int = Field(
        default=5, description="断线重连间隔（秒）", json_schema_extra={"tag": "forward"})
    max_reconnect_attempts: int = Field(
        default=0, description="最大重连次数（0 = 无限重试）", json_schema_extra={"tag": "forward"})
    reverse_ws_host: str = Field(
        default="127.0.0.1",
        description="本地 WS Server 监听地址（非回环地址必须配置 access_token，否则拒绝启动）",
        json_schema_extra={"tag": "reverse"})
    reverse_ws_port: int = Field(
        default=8095, description="本地 WS Server 监听端口（NapCat 连接此端口）",
        json_schema_extra={"tag": "reverse"})
    access_token: str = Field(
        default="", description="访问令牌（与 NapCat 配置一致，留空则不鉴权）",
        json_schema_extra=_PASSWORD)
    http_api_url: str = Field(
        default="", description="HTTP API 地址（可选，留空则通过 WS 发送）")
    self_id: str = Field(
        default="", description="Bot QQ 号（可选，用于判断 @bot）")
    napcat_webui_url: str = Field(
        default="http://127.0.0.1:6099/webui/",
        description="NapCat WebUI 地址（连接成功后可在通道页内嵌浏览）",
        json_schema_extra=_URL)
    require_mention: bool = Field(
        default=True,
        description="群聊中是否需要 @ bot 才激活思考（私聊不受影响，所有消息仍会记录到对话历史）")
    reply_to_mode: Literal["first", "all", "off"] = Field(
        default="first", description="回复引用策略（first=仅首条分段挂引用 / all=全部挂引用 / off=不引用）")
    whitelist_enabled: bool = Field(
        default=False, description="是否启用白名单（开启后仅白名单内的群/用户消息会被处理）")
    group_whitelist: str = Field(
        default="", description="群白名单（允许接收消息的群号，多个用英文逗号分隔）",
        json_schema_extra=_TEXT)
    user_whitelist: str = Field(
        default="", description="用户白名单（允许私聊的 QQ 号，多个用英文逗号分隔）",
        json_schema_extra=_TEXT)


CONFIG_MODEL = QQConfig
