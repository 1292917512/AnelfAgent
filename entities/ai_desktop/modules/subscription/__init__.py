"""订阅额度组件包 — 导入即注册，并对测试/工具重导出供应商定义。"""

from entities._sdk import register_provider_key

from .module import SubscriptionModule
from .providers import PROVIDERS, ProviderSpec

__all__ = ["PROVIDERS", "ProviderSpec", "SubscriptionModule"]

register_provider_key(
    "bailian_console", domain="subscription", title="百炼控制台授权",
    description="用于订阅额度查询的扫码授权，与模型 API Key 独立。",
    fields=(), editor="bailian-console", owner_title="AI 桌面", destination="/entities/ai_desktop",
)
