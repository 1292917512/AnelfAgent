"""记忆数量提示的统一口径；身份画像不适用普通记忆的容量与合并策略。"""

from core.config import get_config_int


def memory_warnings(counts: dict[str, int]) -> list[str]:
    warn = get_config_int("memory_warn_threshold", 200)
    maximum = get_config_int("memory_max_per_type", 500)
    warnings: list[str] = []
    for kind, count in counts.items():
        if kind in {"entity", "permanent"}:
            continue
        if count >= maximum:
            warnings.append(f"{kind} 已达上限 ({count}/{maximum})，请检查重复和过时事实，保留来源与条件")
        elif count >= warn:
            warnings.append(f"{kind} 数量较多 ({count}/{warn})，可按需检查；数量不代表内容有误")
    return warnings
