"""LLM 韧性层 — 错误分类 / 恢复策略 / 主动限流。

与业务无关的可靠性原语收编于此，上层（mind / think_loop / services）
不再各自实现错误处理分支：

- classifier:   异常 → ErrorCategory + 策略标志（retryable/should_compress/should_fallback）
- recovery:     策略标志 → 具体动作（退避重试 / 跳过无效回退 / 压缩后重试）
- rate_limit:   配额窗口主动降频（未配置配额时零开销放行）

旧路径 ``agent.llm.error_classifier`` 保留兼容导入，下版本移除。
"""

from agent.llm.resilience.classifier import (  # noqa: F401
    ClassifiedError,
    ErrorCategory,
    classify_llm_error,
)
from agent.llm.resilience.recovery import (  # noqa: F401
    is_overflow_error,
    next_fallback_index,
    should_try_fallback_candidate,
)
