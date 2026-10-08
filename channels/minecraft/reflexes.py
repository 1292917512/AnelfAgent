"""Minecraft 反射层：零模型调用的确定性生存反射。

设计参考 mindcraft 的 modes 思想——反射是每轮询周期跑的确定性代码，
不经 LLM：LLM 只负责聊天与规划，受伤逃跑、卡死自救这类生存本能必须
零延迟。与 mindcraft 的差异：combat 工具组被禁用，没有反击能力，
自卫统一为逃跑（flee_from）；执行器是第三方包，反射在 Python 侧经
MCP 只读快照判定、确定性工具动作执行。

规则优先级（高优先可打断低优先，同级不叠加）：
1. 死亡重生：血量读取不到（已死）→ respawn + 播报，复活 episode 自动收口
2. 受伤逃逸：血量下降 → 逃离最近敌对生物（没有就离开原位），逃逸后
   给宽限期，卡死自救不许取消进行中的逃离
3. 卡死自救：寻路目标激活但位置冻结超过 stuck_seconds → 停寻路 + 起跳，
   仍冻结则报告玩家（跟随等常驻目标由执行器自行重挂，不代管）
4. 夜间插火把：天黑 + 无任务在身 + 背包有火把 → 脚边插一根（限频）
5. 就近捡拾：无任务在身 + 脚边有掉落物 → 走过去捡起（只捡自己脚边的，
   不远征、不碰玩家远处的物资）

仲裁纪律：反射只动寻路与控制键，不碰背包/挖掘/建造工具（插火把除外，
place_block 是反射的自有动作），与工人（mc-worker）的工具循环互不重叠；
每次需要玩家知情的反射都在游戏内播报，沟通者从聊天事件自然得知上下文。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Awaitable, Callable

from core.log import log

from .protocol import ONLINE_STATE

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
_ESCAPE_GRACE_SECONDS = 10.0
_ANNOUNCE_COOLDOWN_SECONDS = 20.0

# 慢反射（限频，避免每拍都查）：插火把与捡掉落物的最小间隔
_TORCH_CHECK_INTERVAL = 30.0
_TORCH_COOLDOWN = 60.0
_PICKUP_CHECK_INTERVAL = 15.0
_PICKUP_COOLDOWN = 30.0
_PICKUP_SCAN_DISTANCE = 5.0
_PICKUP_GOTO_TIMEOUT_MS = 8000

_ESCAPE_ANNOUNCE = "疼疼疼——我先撤一下！"
_STUCK_ANNOUNCE = "我好像被卡住了……先挣扎两下，不行你拉我一把。"
_DEATH_ANNOUNCE = "我死了……马上重生回来，等我！"

# 可插的火把类物品名（与背包物品名小写精确匹配）
_TORCH_NAMES = frozenset({"torch", "soul_torch", "redstone_torch"})

MCPToolCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Announce = Callable[[str], Awaitable[None]]
Clock = Callable[[], float]
Spawn = Callable[[Awaitable[None]], None]


class ReflexEngine:
    """按固定间隔观察快照并触发反射动作的独立循环。

    tick() 与快照获取分离：单元测试直接喂快照，run() 负责真实 MCP 轮询。
    spawn 钩子把长动作（捡掉落物的 goto）挂到后台任务，不阻塞观察循环；
    缺省（None）时退化为同步执行，仅供测试。
    """

    def __init__(
        self,
        call: MCPToolCall,
        announce: Announce,
        *,
        clock: Clock = time.monotonic,
        spawn: Spawn | None = None,
    ) -> None:
        self._call = call
        self._announce = announce
        self._clock = clock
        self._spawn = spawn
        self._prev_health: float | None = None
        self._prev_position: tuple[float, float, float] | None = None
        self._frozen_for = 0.0
        self._grace_until = 0.0
        self._announced_at: dict[str, float] = {}
        self._dead = False
        self._next_torch_check = 0.0
        self._next_pickup_check = 0.0
        self._picking = False
        self._autoeat_enabled = False

    async def run(self, interval_seconds: float, stop: asyncio.Event) -> None:
        """轮询快照直到 stop；单个周期异常只记日志，反射循环永不退出。"""
        last_observed: float | None = None
        while not stop.is_set():
            try:
                conn = await self._call("get_connection_status", {})
                if conn.get("status") == ONLINE_STATE:
                    if not self._autoeat_enabled:
                        # 连接建立即开自动进食（防饿死是生存闭环的一部分）；
                        # 断线复位，重连后自动补上
                        with contextlib.suppress(Exception):
                            await self._call("autoeat_set_enabled", {"enabled": True})
                            self._autoeat_enabled = True
                    state = await self._call("get_state", {})
                    pathfinder = await self._call("pathfinder_status", {})
                    now = self._clock()
                    dt = 0.0 if last_observed is None else max(0.0, now - last_observed)
                    await self.tick(state, pathfinder, dt)
                    last_observed = now
                else:
                    self._reset()
                    self._autoeat_enabled = False
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
            if health is None:
                # 血量读不到 = 已死（或维度切换首帧）：死亡重生优先于一切
                await self._respawn()
                return
            self._dead = False
            if self._should_flee(health):
                await self._flee(state)
            elif not self._in_grace() and self._is_stuck(pathfinder, position, dt):
                await self._unstuck()
            elif not _goal_active(pathfinder):
                await self._maybe_place_torch(state)
                await self._maybe_pickup()
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
        if (
            not _goal_active(pathfinder)
            or pathfinder.get("isMoving") is not True
            or pathfinder.get("isMining") is True
            or pathfinder.get("isBuilding") is True
            or position is None
            or self._prev_position is None
        ):
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
        # 逃逸宽限期：卡死自救不许取消正在进行的逃离（撞墙被围时寻路
        # 天然冻结，但停掉 flee 目标等于把 bot 钉在敌对生物脸上）
        self._grace_until = self._clock() + _ESCAPE_GRACE_SECONDS
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

    async def _respawn(self) -> None:
        """死亡 episode 内重生一次并播报；复活后 episode 自动收口。"""
        if self._dead:
            return
        self._dead = True
        self._frozen_for = 0.0
        with contextlib.suppress(Exception):
            await self._call("respawn", {})
        await self._announce_once("death", _DEATH_ANNOUNCE)

    async def _maybe_place_torch(self, state: dict[str, Any]) -> None:
        """天黑且无任务在身时脚边插一根火把（限频；无火把则静默跳过）。"""
        now = self._clock()
        if now < self._next_torch_check:
            return
        self._next_torch_check = now + _TORCH_CHECK_INTERVAL
        if (state.get("time") or {}).get("isDay") is not False:
            return  # 白天或时间未知都不插
        position = _as_position(state.get("position"))
        if position is None or state.get("onGround") is False:
            return
        try:
            inventory = await self._call("get_inventory", {})
        except Exception as exc:
            log(f"Minecraft 反射层查背包失败: {exc}", "WARNING", tag="Minecraft")
            return
        torch = _find_torch(inventory.get("items") or [])
        if torch is None:
            return
        self._next_torch_check = now + _TORCH_COOLDOWN
        with contextlib.suppress(Exception):
            await self._call(
                "place_block",
                {
                    "referenceX": position[0],
                    "referenceY": position[1] - 1,
                    "referenceZ": position[2],
                    "faceVector": {"x": 0, "y": 1, "z": 0},
                    "itemName": torch,
                },
            )

    async def _maybe_pickup(self) -> None:
        """无任务在身时捡起脚边掉落物（只捡自己 5 格内的，不远征）。"""
        now = self._clock()
        if self._picking or now < self._next_pickup_check:
            return
        self._next_pickup_check = now + _PICKUP_CHECK_INTERVAL
        try:
            drops = await self._call(
                "list_entities",
                {"name": "item", "maxDistance": _PICKUP_SCAN_DISTANCE, "limit": 3},
            )
        except Exception as exc:
            log(f"Minecraft 反射层扫描掉落物失败: {exc}", "WARNING", tag="Minecraft")
            return
        nearest = _nearest_drop(drops.get("entities") or [])
        if nearest is None:
            return
        self._next_pickup_check = now + _PICKUP_COOLDOWN
        position = _as_position(nearest.get("position"))
        if position is None:
            return
        self._picking = True
        if self._spawn is not None:
            self._spawn(self._goto_drop(position))
        else:
            await self._goto_drop(position)

    async def _goto_drop(self, position: tuple[float, float, float]) -> None:
        try:
            with contextlib.suppress(Exception):
                await self._call(
                    "goto",
                    {
                        "goalType": "near",
                        "x": position[0],
                        "y": position[1],
                        "z": position[2],
                        "range": 1,
                        "timeout": _PICKUP_GOTO_TIMEOUT_MS,
                    },
                )
        finally:
            self._picking = False

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


def _goal_active(pathfinder: dict[str, Any]) -> bool:
    """执行器 pathfinder_status 快照里是否有活跃寻路目标。

    真实返回键是 goal；goalSet/isMoving 只是版本差异兜底（isMoving 在撞墙
    卡死时仍可能为 true，不能单独作为依据）。
    """
    return bool(
        pathfinder.get("goal")
        or pathfinder.get("goalSet")
        or pathfinder.get("goal_set")
        or pathfinder.get("isMoving")
    )


def _find_torch(items: list[dict[str, Any]]) -> str | None:
    """背包物品里找可插的火把，返回物品名（供 place_block itemName）。"""
    for item in items:
        name = str(item.get("name") or "").casefold()
        if name in _TORCH_NAMES:
            return str(item["name"])
    return None


def _nearest_drop(entities: list[dict[str, Any]]) -> dict[str, Any] | None:
    drops = [e for e in entities if str(e.get("name") or "").casefold() == "item"]
    if not drops:
        return None
    return min(drops, key=lambda e: _as_float(e.get("distance")) or float("inf"))


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
