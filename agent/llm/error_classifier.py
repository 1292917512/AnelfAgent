"""兼容 shim — 已迁移至 agent.llm.resilience.classifier。

保留本模块仅为不破坏既有导入（tests / 旧代码），新代码请从
agent.llm.resilience 导入。下个版本删除。
"""

from agent.llm.resilience.classifier import (  # noqa: F401
    ClassifiedError,
    ErrorCategory,
    classify_llm_error,
)
