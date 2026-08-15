"""后台任务唤醒预算 — 防自我激励循环（对齐 dsh tool-jobs 的 maxConsecutiveWakes）。

问题场景：后台任务完成 → 触发新回复周期 → 模型在回复中又启动后台任务 →
完成 → 再唤醒……元决策有节流，但这条链路本身若无上限会自我激励地烧 token。

机制（对齐 dsh 语义）：
- per-scope 计数：连续"无真人输入的自动唤醒"次数；
- 超预算（``background_wake_budget``，默认 3）→ 不再触发新回复周期（完成
  信息仍写短期记忆，下次真人消息触发时模型可见，信息不丢）；
- **真人输入到达即重置**（``reset``）——用户参与后链路重新合法；
- 预算只约束"自动唤醒"这一种触发源，真人消息/定时提醒等原有唤醒不受影响。

Model Experience：本模块纯调度侧，不向模型上下文注入任何内容；
被抑制的完成通知经短期记忆通道（volatile 层）延迟可见，不影响缓存前缀。
"""

from __future__ import annotations

from typing import Dict

from core.config import get_config_int
from core.log import log


class WakeBudgetTracker:
    """per-scope 连续自动唤醒计数器（进程内，Mind 持有）。"""

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}

    @staticmethod
    def _budget() -> int:
        # 0 = 关闭预算（不限制）；负数按 0 处理
        return max(0, get_config_int("background_wake_budget", 3))

    def allow(self, scope: str) -> bool:
        """判定该 scope 是否还允许一次自动唤醒（不递增计数）。

        预算为 0（关闭）时恒放行；计数由 ``consume`` 在实际唤醒后递增。
        """
        if not scope:
            return True
        budget = self._budget()
        if budget <= 0:
            return True
        return self._counts.get(scope, 0) < budget

    def consume(self, scope: str) -> None:
        """实际执行了一次自动唤醒后递增计数。"""
        if not scope:
            return
        self._counts[scope] = self._counts.get(scope, 0) + 1

    def reset(self, scope: str) -> None:
        """真人输入到达：重置该 scope 的连续计数，链路重新合法。"""
        if scope and scope in self._counts:
            self._counts.pop(scope, None)

    def count(self, scope: str) -> int:
        return self._counts.get(scope, 0)

    def note_suppressed(self, scope: str, description: str) -> None:
        log(
            f"后台任务唤醒被预算抑制 [{scope}]（连续自动唤醒已达上限 "
            f"{self._budget()}，等待真人输入重置）: {description[:80]}",
            "WARNING", tag="后台",
        )


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_WAKE_BUDGET_CONFIGS = {
    "mind/background": {
        "background_wake_budget": {
            "description": "后台任务自动唤醒预算：连续无真人输入的自动唤醒超过该次数后"
                           "不再触发新回复周期（完成信息仍写短期记忆，防自我激励循环）；"
                           "真人消息到达即重置。0 = 关闭限制",
            "default": 3,
            "unit": "次",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_WAKE_BUDGET_CONFIGS)
