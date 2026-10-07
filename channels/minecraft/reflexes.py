"""Minecraft 反射层：零模型调用的确定性生存反射。

设计参考 mindcraft 的 modes 思想——反射是每轮询周期跑的确定性代码，
不经 LLM：LLM 只负责聊天与规划，受伤逃跑、卡死自救这类生存本能必须
零延迟。与 mindcraft 的差异：combat 工具组被禁用，没有反击能力，
自卫统一为逃跑（flee_from）；执行器是第三方包，反射在 Python 侧经
MCP 只读快照判定、确定性工具动作执行。

规则优先级（高优先可打断低优先，同级不叠加）：
1. 受伤逃逸：血量下降或低于阈值 → 逃离最近敌对生物（没有就离开原位）
2. 卡死自救：寻路目标激活但位置冻结超过 stuck_seconds → 停寻路 + 起跳，
   仍冻结则报告玩家（跟随等常驻目标由执行器自行重挂，不代管）

仲裁纪律：反射只动寻路与控制键，不碰背包/挖掘/建造工具，与工人
（mc-worker）的工具循环互不重叠；每次反射都在游戏内播报，沟通者
从聊天事件自然得知上下文。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Awaitable, Callable

from core.log import log

# 敌对生物名（Java 版常见全集；匹配 list_entities 返回的 name，小写比较）
_HOSTILE_NAMES = frozenset(
    {
        "zombie", "zombie_villager", "skeleton", "stray", "creeper", "spider",
        "cave_spider", "enderman", "witch", "slime", "magma_cube", "blaze",
        "ghast", "drowned", "husk", "phantom", "pillager", "vindicator",
        "evoker", "ravager", "vex", "silverfish", "guardian", "elder_guardian",
        "shulker", "endermite", "hoglin", "piglin", "piglin_brute", "zoglin",
        "warden", "breeze", "wolf", "bee", "polar_bear", "panda", "llama",
        "trader_llama", "iron_golem", "snow_golem", "dolphin",
    }
)

# 判定阈值（模块级常量：反射是本能，不给用户调参；可调的是开关与轮询间隔）
_FLEE_DISTANCE = 10.0
_THREAT_SCAN_DISTANCE = 12.0
_STUCK_SECONDS = 8
_STUCK_MOVE_EPSILON = 0.15
_STUCK_GRACE_SECONDS = 3.0
_ANNOUNCE_COOLDOWN_SECONDS = 20.0
_ESCAPE_ANNOUNCE = "疼疼疼——我先撤一下！"
_STUCK_ANNOUNCE = "我好像被卡住了……先挣扎两下，不行你拉我一把。"

MCPToolCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Announce = Callable[[str], Awaitable[None]]
Clock = Callable[[], float]


class ReflexEngine:
    """按固定间隔观察快照并触发反射动作的独立循环。

    tick() 与快照获取分离：单元测试直接喂快照，run() 负责真实 MCP 轮询。
    """

    def __init__(
        self,
        call: MCPToolCall,
        announce: Announce,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._call = call
        self._announce = announce
        self._clock = clock
        self._prev_health: float | None = None
        self._prev_position: tuple[float, float, float] | None = None
        self._frozen_for = 0.0
        self._grace_until = 0.0
        self._announced_at: dict[str, float] = {}

    async def run(self, interval_seconds: float, stop: asyncio.Event) -> None:
        """轮询快照直到 stop；单个周期异常只记日志，反射循环永不退出。"""
        last_observed: float | None = None
        while not stop.is_set():
            try:
                conn = await self._call("get_connection_status", {})
                if conn.get("status") == "connected":
                    state = await self._call("get_state", {})
                    pathfinder = await self._call("pathfinder_status", {})
                    now = self._clock()
                    dt = 0.0 if last_observed is None else max(0.0, now - last_observed)
                    await self.tick(state, pathfinder, dt)
                    last_observed = now
                else:
                    self._reset()
                    last_observed = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Minecraft 反射层观察失败: {exc}", "WARNING", tag="Minecraft")
            await asyncio.sleep(interval_seconds)

    def _reset(self) -> None:
        """断线或维度切换等场景清基线，避免重连后误判冻结/掉血。"""
        self._prev_health = None
        self._prev_position = None
        self._frozen_for = 0.0

    async def tick(
        self, state: dict[str, Any], pathfinder: dict[str, Any], dt: float
    ) -> None:
        """喂入一次观察快照（dt 为距上次观察的秒数），按优先级评估并执行。"""
        health = _as_float(state.get("health"))
        position = _as_position(state.get("position"))
        try:
            if self._should_flee(health):
                await self._flee(state)
            elif not self._in_grace() and self._is_stuck(pathfinder, position, dt):
                await self._unstuck()
        finally:
            if health is not None:
                self._prev_health = health
            if position is not None:
                self._prev_position = position

    def _should_flee(self, health: float | None) -> bool:
        if health is None or self._prev_health is None:
            return False  # 首帧只建基线，不依据单帧阈值逃跑
        return health < self._prev_health

    def _is_stuck(
        self, pathfinder: dict[str, Any], position: tuple[float, float, float] | None, dt: float
    ) -> bool:
        # 执行器 pathfinder_status 的真实返回键是 goal；goalSet/isMoving 只是
        # 版本差异兜底（isMoving 在撞墙卡死时仍可能为 true，不能单独作为依据）
        goal_active = bool(
            pathfinder.get("goal")
            or pathfinder.get("goalSet")
            or pathfinder.get("goal_set")
            or pathfinder.get("isMoving")
        )
        if not goal_active or position is None or self._prev_position is None:
            self._frozen_for = 0.0
            return False
        moved = max(
            abs(position[0] - self._prev_position[0]),
            abs(position[2] - self._prev_position[2]),
        )
        self._frozen_for = self._frozen_for + dt if moved < _STUCK_MOVE_EPSILON else 0.0
        return self._frozen_for >= _STUCK_SECONDS

    async def _flee(self, state: dict[str, Any]) -> None:
        """逃离最近敌对生物；没有可见威胁就离开当前位置若干格。"""
        target: dict[str, Any] | None = None
        try:
            entities = await self._call(
                "list_entities", {"maxDistance": _THREAT_SCAN_DISTANCE, "limit": 24}
            )
            target = _nearest_hostile(entities.get("entities") or [])
        except Exception as exc:
            log(f"Minecraft 反射层扫描威胁失败: {exc}", "WARNING", tag="Minecraft")
        position = _as_position(state.get("position"))
        if target is not None and target.get("position"):
            flee_point = target["position"]
        elif position is not None:
            flee_point = {"x": position[0], "y": position[1], "z": position[2]}
        else:
            await self._announce_once("escape", _ESCAPE_ANNOUNCE)
            return
        with contextlib.suppress(Exception):
            await self._call(
                "flee_from",
                {
                    "x": flee_point["x"],
                    "y": flee_point["y"],
                    "z": flee_point["z"],
                    "distance": _FLEE_DISTANCE,
                },
            )
        await self._announce_once("escape", _ESCAPE_ANNOUNCE)

    async def _unstuck(self) -> None:
        """停掉卡死的寻路并起跳挣脱；给宽限期让重挂的目标生效。"""
        self._frozen_for = 0.0
        self._grace_until = self._clock() + _STUCK_GRACE_SECONDS
        for name in ("stop_pathfinding", "clear_control_states"):
            with contextlib.suppress(Exception):
                await self._call(name, {})
        with contextlib.suppress(Exception):
            await self._call("set_control_state", {"control": "jump", "state": True})
            await asyncio.sleep(0.3)
            await self._call("set_control_state", {"control": "jump", "state": False})
        await self._announce_once("stuck", _STUCK_ANNOUNCE)

    def _in_grace(self) -> bool:
        return self._clock() < self._grace_until

    async def _announce_once(self, key: str, text: str) -> None:
        """同类反射事件在冷却期内只播报一次，防每轮重复刷屏。"""
        now = self._clock()
        if now - self._announced_at.get(key, -_ANNOUNCE_COOLDOWN_SECONDS) < _ANNOUNCE_COOLDOWN_SECONDS:
            return
        self._announced_at[key] = now
        with contextlib.suppress(Exception):
            await self._announce(text)


def _nearest_hostile(entities: list[dict[str, Any]]) -> dict[str, Any] | None:
    hostiles = [
        e for e in entities if str(e.get("name") or "").casefold() in _HOSTILE_NAMES
    ]
    if not hostiles:
        return None
    return min(hostiles, key=lambda e: _as_float(e.get("distance")) or float("inf"))


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_position(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, dict):
        return None
    x, y, z = (_as_float(value.get(k)) for k in ("x", "y", "z"))
    if x is None or y is None or z is None:
        return None
    return (x, y, z)
