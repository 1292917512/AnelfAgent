"""AcFun 频道配置 — acfunsdk（账号密码登录 + 通知轮询 + 直播）。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/acfun，键 acfun_<field>），值统一存 ConfigManager。
登录凭据（cookie）与轮询游标属敏感/状态数据，由 ``state.py`` 落入数据目录。
"""

from __future__ import annotations

from pydantic import Field

from agent.channel.base import ChannelConfig

_PASSWORD = {"value_type": "password"}
_TEXT = {"value_type": "text"}


class AcfunConfig(ChannelConfig):
    """AcFun 频道配置（敏感凭据不落配置面，见 state.py）。"""

    enabled: bool = Field(default=False, description="是否启用 AcFun 频道")
    username: str = Field(
        default="", description="AcFun 账号（手机号/用户名，Web 登录后自动回填）")
    password: str = Field(
        default="", description="AcFun 密码（可选；仅用于无头自登录，推荐留空走频道页 Web 登录）",
        json_schema_extra=_PASSWORD)
    poll_interval_seconds: int = Field(default=60, description="通知轮询间隔（秒，最低 15）")
    notify_like: bool = Field(
        default=False,
        description="接收点赞通知（默认仅计数统计，不进会话历史防刷屏；需回复由 like_trigger_mind 控制）")
    notify_gift: bool = Field(default=True, description="接收投蕉（香蕉礼物）通知")
    notify_system: bool = Field(
        default=True, description="接收站内公告 / 系统通知（关注、过审等，仅记录不触发回复）")
    gift_trigger_mind: bool = Field(default=True, description="投蕉通知触发思考回复（向对方致谢）")
    like_trigger_mind: bool = Field(
        default=False, description="点赞通知触发思考回复（默认仅记录，避免点赞风暴刷屏）")
    live_danmaku_cooldown_seconds: int = Field(
        default=5, description="同一直播间弹幕发送冷却（秒，防刷屏）")
    whitelist_enabled: bool = Field(
        default=False, description="是否启用用户白名单（开启后仅白名单用户的互动会被处理）")
    user_whitelist: str = Field(
        default="", description="用户白名单（允许互动的 AcFun UID，多个用英文逗号分隔）",
        json_schema_extra=_TEXT)
    message_max_length: int = Field(
        default=1000, description="单条评论/弹幕最大长度，超出自动分段发送")
    live_mode: bool = Field(
        default=False,
        description="直播模式（连接观察中的直播间，实时接收弹幕并注入上下文；AI 可经 acfun_live_mode 工具实时开关）")
    live_watch_rooms: str = Field(
        default="", description="观察的直播间主播 UID（逗号分隔，直播模式开启时连接）",
        json_schema_extra=_TEXT)
    live_recent_window: int = Field(default=20, description="上下文注入的最近弹幕条数")
    live_mention_names: str = Field(
        default="", description="直播弹幕额外点名词（逗号分隔，命中即触发思考回复）",
        json_schema_extra=_TEXT)
    live_mention_trigger: bool = Field(default=True, description="弹幕点名（bot 名/点名词）触发思考")
    live_gift_trigger_mind: bool = Field(
        default=True, description="直播间礼物/投蕉事件触发思考（致谢流程）")
    live_record_chatter: bool = Field(
        default=False, description="普通弹幕也写入会话历史（默认仅进上下文缓冲，防高流量刷屏）")
    live_max_rooms: int = Field(default=3, description="同时观察的直播间上限")
    live_closed_retry_seconds: int = Field(default=300, description="主播未开播时的重探间隔（秒）")


CONFIG_MODEL = AcfunConfig
