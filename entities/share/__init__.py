"""文件分享推送实体。

目录名 / group 名 / 面板名 / 路由名统一为 share，框架各发现机制自然对齐：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报导航与排序（被 web/routers/config.py 推导）
- register_lifecycle: 启动钩子（被 discover_entity_lifecycles 扫描）
- router.py: build_router()（被 _mount_entity_routers 挂载到 /api/entity/share）
- panel.tsx: 实体监控面板（被 entity-panels glob 发现）
"""

from entities._sdk import entity, entity_manifest

entity("share", "文件分享 - 将工作区文件生成为外部可下载链接")

entity_manifest(
    display_name="文件分享",
    icon="Share2",
    description="将工作区文件生成为外部可下载链接，支持过期策略与手动撤销",
    version="1.0.0",
    order=35,
    nav={"path": "/share", "label": "share", "nav_group": "group_ability"},
    group="share",
)


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

