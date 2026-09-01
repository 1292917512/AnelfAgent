"""AcFun 频道配置 — ConfigRegistry 元数据（供 WebUI 配置页渲染，对齐 QQ/微信频道）。

配置本体存仓库 ``channels/acfun/channel_config.json``（不含机密，Web 编辑/热重载走标准链路）；
登录凭据（cookie）与轮询游标属敏感/状态数据，由 ``state.py`` 落入数据目录。
"""

from __future__ import annotations

from core.config import ConfigValueType

ACFUN_CONFIGS = {
    "adapter/acfun": {
        "enabled": {
            "description": "是否启用 AcFun 频道",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "username": {
            "description": "AcFun 账号（手机号/用户名，Web 登录后自动回填）",
            "default": "",
            "value_type": ConfigValueType.STRING,
        },
        "password": {
            "description": "AcFun 密码（可选；仅用于无头自登录，推荐留空走频道页 Web 登录）",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "poll_interval_seconds": {
            "description": "通知轮询间隔（秒，最低 15）",
            "default": 60,
            "value_type": ConfigValueType.INTEGER,
        },
        "notify_like": {
            "description": "接收点赞通知（默认仅计数统计，不进会话历史防刷屏；需回复由 like_trigger_mind 控制）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "notify_gift": {
            "description": "接收投蕉（香蕉礼物）通知",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "notify_system": {
            "description": "接收站内公告 / 系统通知（关注、过审等，仅记录不触发回复）",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "gift_trigger_mind": {
            "description": "投蕉通知触发思考回复（向对方致谢）",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "like_trigger_mind": {
            "description": "点赞通知触发思考回复（默认仅记录，避免点赞风暴刷屏）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "live_danmaku_cooldown_seconds": {
            "description": "同一直播间弹幕发送冷却（秒，防刷屏）",
            "default": 5,
            "value_type": ConfigValueType.INTEGER,
        },
        "whitelist_enabled": {
            "description": "是否启用用户白名单（开启后仅白名单用户的互动会被处理）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "user_whitelist": {
            "description": "用户白名单（允许互动的 AcFun UID，多个用英文逗号分隔）",
            "default": "",
            "value_type": ConfigValueType.TEXT,
        },
        "message_max_length": {
            "description": "单条评论/弹幕最大长度，超出自动分段发送",
            "default": 1000,
            "value_type": ConfigValueType.INTEGER,
        },
        "live_mode": {
            "description": "直播模式（连接观察中的直播间，实时接收弹幕并注入上下文；AI 可经 acfun_live_mode 工具实时开关）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "live_watch_rooms": {
            "description": "观察的直播间主播 UID（逗号分隔，直播模式开启时连接）",
            "default": "",
            "value_type": ConfigValueType.TEXT,
        },
        "live_recent_window": {
            "description": "上下文注入的最近弹幕条数",
            "default": 20,
            "value_type": ConfigValueType.INTEGER,
        },
        "live_mention_names": {
            "description": "直播弹幕额外点名词（逗号分隔，命中即触发思考回复）",
            "default": "",
            "value_type": ConfigValueType.TEXT,
        },
        "live_mention_trigger": {
            "description": "弹幕点名（bot 名/点名词）触发思考",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "live_gift_trigger_mind": {
            "description": "直播间礼物/投蕉事件触发思考（致谢流程）",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "live_record_chatter": {
            "description": "普通弹幕也写入会话历史（默认仅进上下文缓冲，防高流量刷屏）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "live_max_rooms": {
            "description": "同时观察的直播间上限",
            "default": 3,
            "value_type": ConfigValueType.INTEGER,
        },
        "live_closed_retry_seconds": {
            "description": "主播未开播时的重探间隔（秒）",
            "default": 300,
            "value_type": ConfigValueType.INTEGER,
        },
    }
}
