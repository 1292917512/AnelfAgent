"""HTTP 接口频道配置。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/http_api，键 http_api_<field>），值统一存 ConfigManager。
"""

from __future__ import annotations

from pydantic import Field, model_validator

from agent.channel.base import ChannelConfig


class HttpApiConfig(ChannelConfig):
    """HTTP 接口频道配置。"""

    enabled: bool = Field(default=False, description="是否启用 HTTP 接口频道")
    host: str = Field(default="127.0.0.1", description="HTTP 监听地址")
    port: int = Field(default=8091, ge=1, le=65535, description="HTTP/HTTPS 监听端口")
    reply_timeout: int = Field(default=60, ge=1, description="等待 Agent 回复的超时时间（秒）")
    ssl_certfile: str = Field(default="", description="HTTPS PEM 证书文件，与私钥同时配置")
    ssl_keyfile: str = Field(default="", description="HTTPS PEM 私钥文件")
    ssl_keyfile_password: str = Field(
        default="", description="HTTPS 私钥密码（可选）",
        json_schema_extra={"value_type": "password"})
    public_base_url: str = Field(default="", description="外部接入地址（反向代理时填写，如 https://agent.example.com）")
    stream_queue_size: int = Field(default=256, ge=1, le=10000, description="每个 SSE 订阅的帧缓冲上限，溢出断开慢客户端")
    max_stream_subscribers: int = Field(default=128, ge=1, le=10000, description="同时在线的 SSE 订阅上限")
    max_connections: int = Field(default=256, ge=1, description="HTTP 并发连接/任务上限，超出返回 503")
    sdk_concurrency: int = Field(default=8, ge=1, le=1000, description="SDK 模型和工具并发调用上限，超出返回 503")
    cors_origins: list[str] = Field(default_factory=list, description="允许浏览器跨域访问的来源地址；默认不开放跨域")
    api_token: str = Field(
        default="", description="API Token（空则免认证）",
        json_schema_extra={"value_type": "password"})
    callback_url: str = Field(
        default="", description="默认出站回调地址（Agent 主动消息以 POST JSON 推送到该 webhook）")
    callback_token: str = Field(
        default="", description="出站回调 Bearer Token（空则不带认证头）",
        json_schema_extra={"value_type": "password"})
    callback_timeout: int = Field(default=10, ge=1, description="出站回调请求超时时间（秒）")

    @model_validator(mode="after")
    def validate_transport(self) -> HttpApiConfig:
        """TLS 证书必须成对，公开地址只能是不含凭据的 HTTP(S) 基址。"""
        from urllib.parse import urlsplit

        if bool(self.ssl_certfile) != bool(self.ssl_keyfile):
            raise ValueError("ssl_certfile 与 ssl_keyfile 必须同时配置")
        if self.public_base_url:
            parsed = urlsplit(self.public_base_url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("public_base_url 必须是不含凭据、查询参数或片段的 HTTP(S) 地址")
            self.public_base_url = self.public_base_url.rstrip("/")
        return self

    @property
    def base_url(self) -> str:
        """对外文档使用显式公开地址，否则使用实际监听协议与地址。"""
        if self.public_base_url:
            return self.public_base_url
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{'https' if self.ssl_certfile else 'http'}://{host}:{self.port}"


CONFIG_MODEL = HttpApiConfig
