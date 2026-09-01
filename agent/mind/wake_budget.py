"""后台任务唤醒预算 — 防自我激励循环。

问题场景：后台任务完成 → 触发新回复周期 → 模型在回复中又启动后台任务 →
完成 → 再唤醒……元决策有节流，但这条链路本身若无上限会自我激励地烧 token。

机制：
- per-scope 计数：连续"无真人输入的自动唤醒"次数；成功与失败**各自独立
  计数**（上限同为 ``background_wake_budget``，默认 3）——成功完成被抑制是
  良性的（信息在历史里，下次真人消息可见）；失败完成（任务死了无人告知）
  必须先耗尽独立额度才可被抑制，最坏情况总唤醒数有界（≤ 2×预算）；
- 超预算 → 不再触发新回复周期（完成信息仍写历史，下次真人消息/心跳
  触发时模型可见，信息不丢）；
- **真人输入到达即重置**（``reset``）——用户参与后链路重新合法；
- 预算只约束"自动唤醒"这一种触发源，真人消息/定时提醒等原有唤醒不受影响。

Model Experience：本模块纯调度侧，不向模型上下文注入任何内容；
被抑制的完成通知经对话历史/短期记忆通道延迟可见，不影响缓存前缀。
"""

from __future__ import annotations

from typing import Dict

from core.config import get_config_int
from core.log import log


class WakeBudgetTracker:
    """per-scope 连续自动唤醒计数器（进程内，Mind 持有）。

    成功唤醒与失败唤醒分别计数（``_counts`` / ``_failure_counts``），
    互不挤占——失败通知是用户必须知晓的事实，不因成功唤醒耗尽预算而被吞。
    """

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}
        self._failure_counts: Dict[str, int] = {}

    @staticmethod
    def _budget() -> int:
        # 0 = 关闭预算（不限制）；负数按 0 处理
        return max(0, get_config_int("background_wake_budget", 3))

    def allow(self, scope: str, *, failed: bool = False) -> bool:
        """判定该 scope 是否还允许一次自动唤醒（不递增计数）。

        预算为 0（关闭）时恒放行；计数由 ``consume`` 在实际唤醒后递增。
        failed=True 检查失败唤醒的独立额度。
        """
        if not scope:
            return True
        budget = self._budget()
        if budget <= 0:
            return True
        counts = self._failure_counts if failed else self._counts
        return counts.get(scope, 0) < budget

    def consume(self, scope: str, *, failed: bool = False) -> None:
        """实际执行了一次自动唤醒后递增计数。"""
        if not scope:
            return
        counts = self._failure_counts if failed else self._counts
        counts[scope] = counts.get(scope, 0) + 1

    def reset(self, scope: str) -> None:
        """真人输入到达：重置该 scope 的连续计数，链路重新合法。"""
        if scope:
            self._counts.pop(scope, None)
            self._failure_counts.pop(scope, None)

    def count(self, scope: str, *, failed: bool = False) -> int:
        counts = self._failure_counts if failed else self._counts
        return counts.get(scope, 0)

    def note_suppressed(self, scope: str, description: str, *, failed: bool = False) -> None:
        kind = "失败" if failed else "成功"
        log(
            f"后台任务唤醒被预算抑制 [{scope}]（连续{kind}自动唤醒已达上限 "
            f"{self._budget()}，等待真人输入重置）: {description[:80]}",
            "WARNING", tag="后台",
        )


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_WAKE_BUDGET_CONFIGS = {
    "mind/background": {
        "background_wake_budget": {
            "description": "后台任务自动唤醒预算：连续无真人输入的自动唤醒（成功与"
                           "失败各自独立计数）超过该次数后不再触发新回复周期（完成"
                           "信息仍写对话历史，防自我激励循环）；真人消息到达即重置。"
                           "0 = 关闭限制",
            "default": 3,
            "unit": "次",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_WAKE_BUDGET_CONFIGS)
