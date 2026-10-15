"""判断请求的端到端响应时间阈值，不含任何模型上下文。"""

from core.config import get_config_float


def slow_threshold_ms() -> int:
    """按配置的端到端耗时阈值提示偏慢，独立于请求超时预算。"""
    seconds = max(0.1, get_config_float("judgment_slow_seconds", 3.0))
    return max(1, round(seconds * 1000))
