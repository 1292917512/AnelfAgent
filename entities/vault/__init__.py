"""密码本实体：加密凭据库 + 综合验证中心。

对标 Bitwarden / KeePassXC，AI 优先（Agent 是系统主控）：
- 商业级加密：KEK（机器密钥文件 / 主密码 Argon2id 派生）→ AES-256-GCM 包裹 DEK
  → 逐字段加密 password/totp/notes（AAD 绑定条目 id 防密文调换）；
  模式转换与改主密码仅重包 DEK，条目不重加密
- 双端零摩擦：默认机器密钥模式——首次写入自动建库、启动透明自动解锁，
  AI 即用即取；主密码模式为可选加固（Web 面板启用，闲置自动锁定，
  ANELF_VAULT_PASSWORD 环境变量支持无人值守解锁）
- 检索：多级模糊打分（精确>子串>前缀>bigram>LCS）+ URL 归一化 + 字段加权
- 综合验证中心：TOTP（RFC 6238）/ 强密码生成器 / HIBP k-匿名泄露体检
- 互通：Bitwarden JSON/CSV、Chrome CSV、KeePass CSV 导入；Bitwarden 兼容导出
  与独立密码加密备份

Model Experience：
① 模型看到什么——vault 工具组默认沉睡（目录仅 sleep_brief），context provider
   注入解锁模式与条目数（非密，~30 token，volatile 层）
② token 影响——沉睡态零 schema 成本，激活后 11 个工具；检索/详情返回紧凑 JSON
③ 缓存影响——仅 volatile 尾部动态区，不触碰任何前缀层

审批建议（risk meta 仅声明，实际拦截走权限规则引擎）：
在审批规则页为 vault_reveal / vault_totp / vault_delete 配置 ask 规则
（risk_level=critical），敏感明文凭据经人工确认后放行。
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("vault", "密码本 - 加密凭据库 / TOTP 验证器 / 模糊检索 / 泄露体检")

entity_manifest(
    display_name="密码本",
    icon="key-round",
    description="加密密码库：主密码解锁、TOTP 验证器、模糊检索、泄露体检、导入导出",
    version="1.0.0",
    order=25,
    group="vault",
)

# 实体配置项：分组 entity/vault，实体详情页配置 tab 自动展示
register_configs_safe({
    "entity/vault": {
        "vault_ai_enabled": {
            "description": "是否允许 AI 调用密码本工具（检索/增删改查/取验证码）",
            "default": True,
        },
        "vault_context_inject": {
            "description": "是否向 AI 上下文注入密码本状态（解锁模式与条目数，非密）",
            "default": True,
        },
        "vault_auto_setup_enabled": {
            "description": "未初始化时是否自动以机器密钥模式创建密码本（AI 首次写入即用）",
            "default": True,
        },
        "vault_auto_lock_minutes": {
            "description": "解锁后闲置自动锁定时间（每次使用顺延）",
            "default": 15,
            "value_type": "range", "min": 1, "max": 240, "step": 1,
            "unit": "分钟",
        },
        "vault_breach_check_enabled": {
            "description": "是否启用 HIBP 联网泄露检查（k-匿名，明文不出本机）",
            "default": True,
        },
        "vault_generator_default_length": {
            "description": "AI 自动生成密码的默认长度",
            "default": 20,
            "advanced": True,
            "value_type": "range", "min": 8, "max": 64, "step": 1,
        },
        "vault_kdf_time_cost": {
            "description": "Argon2id 迭代轮数（仅影响设置/更换主密码时的派生）",
            "default": 3,
            "advanced": True,
        },
        "vault_kdf_memory_cost": {
            "description": "Argon2id 内存开销（KiB）",
            "default": 65536,
            "advanced": True,
            "unit": "KiB",
        },
        "vault_kdf_parallelism": {
            "description": "Argon2id 并行度",
            "default": 4,
            "advanced": True,
        },
    }
})


def register_lifecycle() -> None:
    """注册密码本服务生命周期（进程退出时锁定并关闭存储）。"""
    from core.lifecycle import Lifecycle

    from .service import get_vault_service
    service = get_vault_service()
    Lifecycle.register("vault_service", service,
                       on_start=service.initialize, cleanup=service.close)


from . import context, tools  # noqa: F401, E402  # 注册上下文提供者 + 触发 @tool 注册
