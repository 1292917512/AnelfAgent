"""SSH 远程管理实体。

完整的 SSH 客户端能力，供 AI 与 Web 用户共同使用：
- 连接配置管理（增删改查 + 默认连接切换，凭据脱敏存储）
- 连接池复用（keepalive 保活 + 失效连接自动重建）
- 结构化命令执行（exit_code/stdout/stderr）
- SFTP 文件上传/下载
- 实时状态经 SSE 推送，激活连接经 context_provider 注入 AI 上下文

目录名 / group 名 / 面板名 / 路由名统一为 ssh，框架各发现机制自然对齐：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报展示信息（名称/图标/排序）
- register_configs_safe: 实体配置项（实体详情页配置 tab 展示）
- register_lifecycle: 启动钩子（被 discover_entity_lifecycles 扫描）
- router.py: build_router()（被 _mount_entity_routers 挂载到 /api/entity/ssh）
- panel.tsx: 实体管理面板（被 entity-panels glob 发现）
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("ssh", "SSH 远程管理 - 连接管理、命令执行、文件传输")

entity_manifest(
    display_name="SSH 远程管理",
    icon="terminal",
    description="完整 SSH 客户端：连接管理、命令执行、文件传输，AI 与用户共用",
    version="1.0.0",
    order=30,
    group="ssh",
)

# 实体配置项：分组名 entity/ssh，实体详情页配置 tab 自动展示
register_configs_safe({
    "entity/ssh": {
        "ssh_ai_enabled": {
            "description": "是否允许 AI 调用 SSH 工具（连接/执行/传输）",
            "default": True,
        },
        "ssh_default_timeout": {
            "description": "命令执行默认超时",
            "default": 60,
            "advanced": True,
            "unit": "秒",
        },
        "ssh_probe_timeout": {
            "description": "建连前 TCP 端口探测超时（不可达主机秒级失败并精确归因，0 表示禁用）",
            "default": 5,
            "advanced": True,
            "unit": "秒",
        },
        "ssh_connect_timeout": {
            "description": "建立连接与会话通道打开超时",
            "default": 15,
            "advanced": True,
            "unit": "秒",
        },
        "ssh_keepalive_interval": {
            "description": "连接保活间隔（用于死链检测）",
            "default": 30,
            "advanced": True,
            "unit": "秒",
        },
        "ssh_verify_host_key": {
            "description": "是否校验主机密钥（关闭则信任所有主机，适合内网/自管服务器）",
            "default": False,
        },
    }
})


def register_lifecycle() -> None:
    """注册连接管理器生命周期（进程退出时关闭所有连接）。"""
    from core.lifecycle import Lifecycle

    from .manager import get_ssh_manager
    manager = get_ssh_manager()
    Lifecycle.register("ssh_manager", manager, cleanup=manager.close_all)


from . import context, tools  # noqa: F401, E402  # 注册上下文提供者 + 触发 @tool 注册
