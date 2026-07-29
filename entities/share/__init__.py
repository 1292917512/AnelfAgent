"""文件分享推送实体 — 自治注册示例。

所有自治钩子都在本文件暴露，由框架自动扫描调用：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时间接触发）
- entity_manifest: 自报导航与排序（被 web/routers/config.py 推导）
- register_lifecycle: 启动钩子（被 discover_entity_lifecycles 扫描）
- router.py: build_router()（被 _mount_entity_routers 扫描挂载到 /api/entity/share）
"""

from entities._sdk import entity, entity_manifest

# 1. 注册 group（LLM 工具目录）
entity("file_share", "文件分享 - 将工作区文件生成为外部可下载链接")

# 2. 自报 manifest（前端导航 + 排序）
entity_manifest(
    display_name="文件分享",
    icon="Share2",
    description="将工作区文件生成为外部可下载链接，支持过期策略与手动撤销",
    version="1.0.0",
    order=35,
    nav={"path": "/share", "label": "share", "nav_group": "group_ability"},
    group="file_share",
)


# 3. 启动钩子（自动注册 Lifecycle）
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


# 4. 触发工具注册（模块副作用）
from . import tools  # noqa: F401, E402
