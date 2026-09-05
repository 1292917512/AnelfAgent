"""飞书频道配置。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/feishu，键 feishu_<field>），值统一存 ConfigManager。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent.channel.base import ChannelConfig

_PASSWORD = {"value_type": "password"}


class FeishuConfig(ChannelConfig):
    """飞书频道配置。"""

    enabled: bool = Field(default=False, description="是否启用飞书频道")
    app_id: str = Field(default="", description="飞书应用 App ID（在飞书开放平台创建应用后获取）")
    app_secret: str = Field(
        default="", description="飞书应用 App Secret", json_schema_extra=_PASSWORD)
    domain: Literal["feishu", "lark"] = Field(
        default="feishu", description="飞书域名（feishu=国内版, lark=国际版）")
    connection_mode: Literal["websocket", "webhook"] = Field(
        default="websocket", description="接入模式（websocket=长连接免公网, webhook=HTTP 回调需公网）")
    encrypt_key: str = Field(
        default="", description="事件加密密钥（Webhook 模式需要，在飞书后台「事件订阅」中获取）",
        json_schema_extra=_PASSWORD)
    verification_token: str = Field(
        default="", description="事件验证令牌（Webhook 模式需要，在飞书后台「事件订阅」中获取）",
        json_schema_extra=_PASSWORD)
    webhook_host: str = Field(
        default="127.0.0.1",
        description="Webhook 监听地址（仅 webhook 模式使用；非回环地址必须配置验证令牌或加密 Key，否则拒绝启动）")
    webhook_port: int = Field(default=9321, description="Webhook 监听端口（仅 webhook 模式使用）")
    require_mention: bool = Field(default=True, description="群聊中是否需要 @Bot 才触发思考")
    reply_to_mode: Literal["first", "all", "off"] = Field(
        default="first", description="回复引用策略（first=仅首条分段挂引用 / all=全部挂引用 / off=不引用）")
    reply_in_thread: bool = Field(
        default=False, description="群聊中引用回复进入话题（thread）模式，长讨论不刷群主聊面板")
    markdown_render: bool = Field(
        default=True, description="含 Markdown 语法（标题/加粗/列表/表格/代码块等）的回复以富文本渲染发送")
    sender_name_enabled: bool = Field(
        default=True,
        description="解析发送者昵称（需为应用开通 contact:user.base:readonly 权限；缺失时自动降级为 open_id）")
    max_download_mb: int = Field(
        default=50, description="入站媒体文件下载大小上限（MB），超出拒绝下载并在日志中说明")
    text_limit: int = Field(default=4000, description="单条消息字符限制（飞书上限约 4000）")


CONFIG_MODEL = FeishuConfig
