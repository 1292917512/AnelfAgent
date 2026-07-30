"""文件分享推送实体。

目录名 / group 名 / 面板名 / 路由名统一为 share，框架各发现机制自然对齐：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报展示信息（名称/图标/排序）
- register_configs_safe: 实体配置项（分组名 = group，实体详情页配置 tab 展示）
- register_lifecycle: 启动钩子（被 discover_entity_lifecycles 扫描）
- router.py: build_router()（被 _mount_entity_routers 挂载到 /api/entity/share）
- panel.tsx: 实体管理面板（被 entity-panels glob 发现，经实体详情页进入）
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("share", "文件分享 - 将工作区文件生成为外部可下载链接")

entity_manifest(
    display_name="文件分享",
    icon="Share2",
    description="将工作区文件生成为外部可下载链接，支持过期策略与手动撤销",
    version="1.0.0",
    order=35,
    group="share",
)

# 实体配置项：分组名与实体 group 一致，实体详情页配置 tab 自动展示
register_configs_safe({
    "share": {
        "share_public_base_url": {
            "description": "公网基址（如 https://your-domain），空则生成相对路径、外部无法访问",
            "default": "",
        },
        "share_default_expires_in": {
            "description": "创建分享链接的默认有效期",
            "default": "24h",
            "options": ["1h", "6h", "24h", "7d", "30d", "never"],
        },
        "share_token_length": {
            "description": "分享链接 token 长度（8-64）",
            "default": 22,
        },
        "share_default_max_downloads": {
            "description": "默认最大下载次数（0 表示无限制）",
            "default": 0,
        },
        "share_ai_auto_share": {
            "description": "允许 AI 主动调用工具创建分享链接",
            "default": True,
        },
        "share_audit_enabled": {
            "description": "记录下载审计日志（IP / UA / 时间）",
            "default": True,
        },
    }
})


def register_lifecycle() -> None:
    from core.lifecycle import Lifecycle

    from .store import get_share_store
    store = get_share_store()
    Lifecycle.register(
        "share_store",
        store,
        cleanup=store.close,
        on_tick=store.sweep_expired,  # 心跳时清理过期链接
    )


from . import tools  # noqa: F401, E402

