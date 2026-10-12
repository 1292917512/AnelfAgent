"""判断请求的端到端响应时间阈值，不含任何模型上下文。"""

from core.config import get_config_float


def slow_threshold_ms(budget_seconds: float) -> int:
    """在配置阈值或等待预算的八成处提示偏慢，以先到者为准。"""
    seconds = min(max(0.1, get_config_float("judgment_slow_seconds", 1.0)), budget_seconds * 0.8)
    return max(1, round(seconds * 1000))
