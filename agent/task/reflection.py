"""自主反思任务的身份与冷却策略，执行历史是跨重启的计时事实源。

模型可见：只有真实自我反思的尝试计入态势，普通内部任务不冒充反思。
Token：冷却判定不调用模型，减少重复反思。
缓存：策略不改变提示前缀；执行事实由动态态势呈现。
"""

from __future__ import annotations

import time

from core.config import get_config_int, register_configs_safe

from .history import get_history

SELF_REFLECTION_TASK = "self_reflection"

register_configs_safe({
    "mind/reflect": {
        "reflection_cooldown_minutes": {
            "description": "自主反思任务两次尝试的最小间隔；按执行历史跨重启生效，普通任务不计入（0 关闭冷却）",
            "default": 30,
            "min": 0,
            "max": 1440,
            "unit": "分钟",
        },
    },
})


def last_reflection_time() -> float:
    """最近自我反思尝试的开始时间，失败和中断也计入以避免立即重放。"""
    return max((float(r.get("started_at") or 0) for r in get_history(SELF_REFLECTION_TASK)), default=0.0)


def reflection_cooldown_reason() -> str:
    """冷却中返回原因，否则返回空串；在任务认领处统一检查。"""
    minutes = max(0, get_config_int("reflection_cooldown_minutes", 30))
    last = last_reflection_time()
    elapsed = max(0.0, (time.time() - last) / 60)
    if minutes and last > 0 and elapsed < minutes:
        return f"反思冷却中：距上次尝试 {elapsed:.0f} 分钟，最小间隔 {minutes} 分钟"
    return ""
