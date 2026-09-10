"""Dify 平台实体 — 连接既有 Dify 实例的桥梁（外部部署优先）。

定位：Dify 由外部环境承载（用户自部署 / 云端 / 内网实例；需要自动部署时
AI 可借助 devops/ssh 等既有能力完成后回本实体连接），本实体负责
"连接 → 接管 → 驱动"，AI 与用户经面板共用同一实现：

- 连接：dify_base_url + 管理员凭据（未初始化的自托管实例可自动建管理员，
  已初始化实例录入凭据接管，dify_connect 一键握手）
- 管理：应用 CRUD / DSL 导出导入与覆盖 / 发布 / API Key 托管 / 模型供应商 / 数据集
- 运行：workflow 运行 / chat 对话（Service API，Key 自动签发托管）
- 桥接：Dify App → MCP Server → 自动注册进 Anelf MCP（热加载，AI 直接调用）
- 上下文注入：dify_status provider 把连接状态注入 AI 每轮上下文

目录名 / group 名 / 面板名 / 路由名统一为 dify，框架各发现机制自然对齐：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报展示信息（实体详情页入口，不占侧边栏导航）
- entity_config: 实体配置项（config.json 生命周期托管，详情页配置 tab 展示）
- register_lifecycle: 进程退出清理钩子（被 discover_entity_lifecycles 扫描）
- router.py: build_router()（自动挂载 /api/entity/dify）
- panel.tsx: 实体管理面板（entity-panels glob 自动发现，详情页 panel tab）
"""

from entities._sdk import entity, entity_config, entity_manifest

entity("dify", "Dify 平台 - 连接既有 Dify 实例：应用与工作流 DSL 管理、运行调用、MCP 桥接")

entity_manifest(
    display_name="Dify 平台",
    icon="Workflow",
    description="连接外部 Dify 实例的桥梁：应用/工作流编排、运行调用、MCP 桥接，AI 与用户共管",
    version="1.0.0",
    order=41,
    group="dify",
)

# 实体配置项：分组 entity/dify，config.json 生命周期由 entity_config 托管，
# 实体详情页配置 tab 自动展示；密钥不进入本配置组（见 config.py 的 secrets.json）
entity_config({
    "entity/dify": {
        "dify_enabled": {
            "description": "是否启用 Dify 实体（关闭后 AI 工具与面板操作被拒）",
            "default": True,
        },
        "dify_base_url": {
            "description": "Dify 实例地址（nginx 入口，如 http://127.0.0.1:8899；留空表示未连接）",
            "default": "",
        },
        "dify_admin_email": {
            "description": "自动初始化时创建的管理员邮箱（仅未初始化的自托管实例生效）",
            "default": "admin@dify.local",
        },
        "dify_auto_setup": {
            "description": "连接到未初始化的自托管实例时自动创建管理员",
            "default": True,
        },
        "dify_context_inject": {
            "description": "是否向 AI 上下文注入 Dify 连接状态摘要",
            "default": True,
        },
        "dify_timeout": {
            "description": "Dify API 请求超时",
            "default": 30,
            "advanced": True,
            "unit": "秒",
        },
    },
})


def register_lifecycle() -> None:
    """注册实体生命周期（进程退出清理钩子）。"""
    from core.lifecycle import Lifecycle

    from .service import get_dify_service
    service = get_dify_service()
    Lifecycle.register("dify_service", service, cleanup=service.aclose)


from . import context, tools  # noqa: F401, E402  # 注册上下文提供者 + 触发 @tool 注册
