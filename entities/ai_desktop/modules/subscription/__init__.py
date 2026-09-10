"""订阅额度组件包 — 导入即注册，并对测试/工具重导出供应商定义。"""

from .module import SubscriptionModule
from .providers import PROVIDERS, ProviderSpec

__all__ = ["PROVIDERS", "ProviderSpec", "SubscriptionModule"]
