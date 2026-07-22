"""微信频道配置 — ConfigRegistry 元数据（供 WebUI 配置页渲染，对齐 QQ 频道）。"""

from __future__ import annotations

from core.config import ConfigValueType

WEIXIN_CONFIGS = {
    "adapter/weixin": {
        "enabled": {
            "description": "是否启用微信频道",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "account_id": {
            "description": "iLink Bot 账号 ID（扫码登录后获得，形如 ...@im.bot）",
            "default": "",
            "value_type": ConfigValueType.STRING,
        },
        "token": {
            "description": "iLink Bot Token（扫码登录后自动保存）",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "base_url": {
            "description": "iLink API 地址",
            "default": "https://ilinkai.weixin.qq.com",
            "value_type": ConfigValueType.URL,
        },
        "cdn_base_url": {
            "description": "微信 CDN 地址（媒体上传/下载）",
            "default": "https://novac2c.cdn.weixin.qq.com/c2c",
            "value_type": ConfigValueType.URL,
        },
        "dm_policy": {
            "description": "私聊访问策略（open=所有人 / allowlist=白名单 / disabled=禁用）",
            "default": "open",
            "value_type": ConfigValueType.ENUM,
            "options": ["open", "allowlist", "disabled"],
        },
        "allow_from": {
            "description": "私聊白名单（用户 ID，多个用英文逗号分隔，dm_policy=allowlist 时生效）",
            "default": "",
            "value_type": ConfigValueType.TEXT,
        },
        "group_policy": {
            "description": "群聊访问策略（注意：iLink bot 身份通常无法接收普通群消息，限制在 iLink 侧）",
            "default": "disabled",
            "value_type": ConfigValueType.ENUM,
            "options": ["open", "allowlist", "disabled"],
        },
        "group_allow_from": {
            "description": "群聊白名单（群聊 ID，多个用英文逗号分隔，group_policy=allowlist 时生效）",
            "default": "",
            "value_type": ConfigValueType.TEXT,
        },
        "split_multiline_messages": {
            "description": "多行消息逐行拆分发送（默认 compact 模式：能放下就整条发）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "typing_indicator": {
            "description": "处理消息时向对方显示「正在输入」状态",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
    }
}
