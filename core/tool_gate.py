"""工具门控 — check_fn 前置条件检查与结果缓存。

工具可声明 ``check_fn``（零参数 callable，返回 bool 或 Awaitable[bool]），
前置条件不满足时工具不出现在 LLM 的 schema 中，避免工具列表膨胀。

缓存策略（参考 hermes-agent registry）：
- 检查结果缓存 ``ttl_seconds``（默认 30s），避免每次请求都探测外部状态
- 瞬态故障宽限：最近 ``failure_grace_seconds``（默认 60s）内成功过的检查，
  偶发失败视为抖动，返回 last-good True 且【不缓存失败】，下次调用重新探测
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Awaitable, Callable, Dict, Optional, Tuple, Union

from core.config import get_config_bool, get_config_float, register_configs_safe
from core.log import log

CheckFn = Callable[[], Union[bool, Awaitable[bool]]]

_CHECK_FN_TTL_SECONDS = 30.0
_CHECK_FN_FAILURE_GRACE_SECONDS = 60.0
_CHECK_FN_TIMEOUT_SECONDS = 5.0


class ToolGate:
    """工具门控：评估 check_fn 并缓存结果（带瞬态故障抑制）。"""

    def __init__(
            self,
            *,
            ttl_seconds: float = _CHECK_FN_TTL_SECONDS,
            failure_grace_seconds: float = _CHECK_FN_FAILURE_GRACE_SECONDS,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.failure_grace_seconds = failure_grace_seconds
        # check_fn -> (单调时钟时间戳, 检查结果)
        self._cache: Dict[CheckFn, Tuple[float, bool]] = {}
        # check_fn -> 最近一次返回 True 的单调时钟时间戳
        self._last_good: Dict[CheckFn, float] = {}

    def _effective_ttl(self) -> float:
        return get_config_float("tool_gate_check_ttl_seconds", self.ttl_seconds)

    def _effective_grace(self) -> float:
        return get_config_float("tool_gate_check_grace_seconds", self.failure_grace_seconds)

    async def _run_check(self, fn: CheckFn) -> bool:
        """执行 check_fn（兼容同步/异步，异步分支 5s 超时），异常一律视为检查失败。"""
        try:
            result = fn()
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=_CHECK_FN_TIMEOUT_SECONDS)
            return bool(result)
        except asyncio.TimeoutError:
            log(f"工具门控 check_fn 超时 ({_CHECK_FN_TIMEOUT_SECONDS}s)", "DEBUG", tag="门控")
            return False
        except Exception as exc:
            log(f"工具门控 check_fn 异常: {type(exc).__name__}: {exc}", "DEBUG", tag="门控")
            return False

    async def check(self, fn: Optional[CheckFn]) -> bool:
        """评估 check_fn（带 TTL 缓存与瞬态故障宽限）。fn 为 None 时视为通过。"""
        if fn is None:
            return True

        now = time.monotonic()
        ttl = self._effective_ttl()
        grace = self._effective_grace()

        cached = self._cache.get(fn)
        if cached is not None:
            ts, value = cached
            if now - ts < ttl:
                return value

        value = await self._run_check(fn)

        if value:
            self._last_good[fn] = now
            self._cache[fn] = (now, True)
            return True

        last_good = self._last_good.get(fn)
        if last_good is not None and now - last_good < grace:
            # 最近成功过 → 视为抖动，返回 last-good True，
            # 且不把失败写入缓存，下次调用重新探测
            log("工具门控检查瞬态失败，宽限期内视为可用", "DEBUG", tag="门控")
            return True

        self._cache[fn] = (now, False)
        return False

    async def filter_names(self, items: Dict[str, Optional[CheckFn]]) -> Dict[str, bool]:
        """批量评估 {名称: check_fn}，同一 fn 在一次评估中只探测一次。

        Returns:
            {名称: 是否通过}
        """
        results: Dict[str, bool] = {}
        # 收集去重后的待探测 fn（None 直接通过），name → fn 标识
        pending: Dict[int, CheckFn] = {}
        name_to_key: Dict[str, int] = {}
        for name, fn in items.items():
            if fn is None:
                results[name] = True
                continue
            fn_key = id(fn)
            name_to_key[name] = fn_key
            pending.setdefault(fn_key, fn)

        # 并发探测去重后的 fn；check 内部自管缓存，无需持锁串行 await
        keys = list(pending.keys())
        outcomes = await asyncio.gather(*(self.check(pending[k]) for k in keys))
        fn_results = dict(zip(keys, outcomes))

        for name, fn_key in name_to_key.items():
            results[name] = fn_results[fn_key]
        return results

    def invalidate(self) -> None:
        """清空全部缓存（下一次评估强制重新探测）。"""
        self._cache.clear()
        log("工具门控缓存已失效", "DEBUG", tag="门控")


# 全局单例：所有工具门控检查共享同一份缓存
tool_gate = ToolGate()


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_TOOL_GATE_CONFIGS = {
    "工具门控": {
        "tool_gate_enabled": {
            "description": "是否启用工具门控（check_fn 前置条件过滤）",
            "default": True,
        },
        "tool_gate_check_ttl_seconds": {
            "description": "check_fn 检查结果缓存时长（秒）",
            "default": _CHECK_FN_TTL_SECONDS,
        },
        "tool_gate_check_grace_seconds": {
            "description": "check_fn 瞬态故障宽限窗口（秒）",
            "default": _CHECK_FN_FAILURE_GRACE_SECONDS,
        },
        "tool_gate_sleep_enabled": {
            "description": "是否启用工具沉睡/激活模式",
            "default": True,
        },
        "tool_gate_default_active_rounds": {
            "description": "工具分组激活后的默认持续轮数",
            "default": 3,
        },
        "tool_gate_max_active_rounds": {
            "description": "工具分组激活持续轮数上限",
            "default": 20,
        },
    },
}

register_configs_safe(_TOOL_GATE_CONFIGS)


def is_gate_enabled() -> bool:
    """工具门控总开关。"""
    return get_config_bool("tool_gate_enabled", True)


def is_sleep_enabled() -> bool:
    """工具沉睡/激活模式开关。"""
    return get_config_bool("tool_gate_sleep_enabled", True)
