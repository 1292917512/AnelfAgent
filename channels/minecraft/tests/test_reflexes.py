"""反射层判定与动作的单元测试：喂快照验证，不触碰真实 MCP。"""

from typing import Any

import pytest

from channels.minecraft import reflexes


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    """记录 MCP 调用并按需返回脚本化响应。"""

    def __init__(
        self,
        entities: list[dict[str, Any]] | None = None,
        inventory_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.entities = entities or []
        self.inventory_items = inventory_items or []
        self.announced: list[str] = []
        self.fail_tools: set[str] = set()

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, args))
        if tool in self.fail_tools:
            raise RuntimeError(f"{tool} 失败")
        if tool == "list_entities":
            return {"count": len(self.entities), "entities": self.entities}
        if tool == "get_inventory":
            return {"items": self.inventory_items}
        return {"ok": True}

    async def announce(self, text: str) -> None:
        self.announced.append(text)

    def tools(self, name: str) -> list[dict[str, Any]]:
        return [args for tool, args in self.calls if tool == name]


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def make_engine(
    recorder: Recorder, clock: FakeClock
) -> reflexes.ReflexEngine:
    return reflexes.ReflexEngine(recorder.call, recorder.announce, clock=clock)


def state(
    health: float | None = 20.0, x: float = 100.0, z: float = 100.0
) -> dict[str, Any]:
    return {"health": health, "position": {"x": x, "y": 64.0, "z": z}}


def pathfinder(active: bool = True) -> dict[str, Any]:
    """真实执行器 pathfinder_status 的返回形状（goal 键）。"""
    return {"goal": active, "isMoving": active}


async def test_background_mining_is_not_idle_even_between_path_goals(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.entities = [{"name": "item", "distance": 1, "position": {"x": 101, "y": 64, "z": 100}}]
    recorder.inventory_items = [{"name": "torch", "count": 32}]
    engine = make_engine(recorder, clock)
    for _ in range(20):
        await engine.tick(state(), {"goal": None, "isMoving": False, "activeAction": "mine_resources"}, dt=1.0)
        clock.advance(1)
    assert recorder.calls == []


async def test_first_tick_only_builds_baseline(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)

    await engine.tick(state(health=20.0), pathfinder(active=True), dt=1.0)

    assert recorder.calls == []
    assert recorder.announced == []


async def test_health_drop_triggers_flee_from_nearest_hostile(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.entities = [
        {"name": "zombie", "position": {"x": 105, "y": 64, "z": 100}, "distance": 5.0},
        {"name": "creeper", "position": {"x": 102, "y": 64, "z": 102}, "distance": 2.8},
        {"name": "cow", "position": {"x": 101, "y": 64, "z": 100}, "distance": 1.0},
    ]
    engine = make_engine(recorder, clock)
    await engine.tick(state(health=20.0), pathfinder(), dt=1.0)
    clock.advance(1.0)

    await engine.tick(state(health=17.0), pathfinder(), dt=1.0)

    flee = recorder.tools("flee_from")
    assert len(flee) == 1
    assert flee[0]["x"] == 102  # 最近敌对是 creeper，不是更近的牛
    assert flee[0]["distance"] == reflexes._FLEE_DISTANCE
    assert recorder.announced == [reflexes._ESCAPE_ANNOUNCE]


async def test_flee_without_hostile_uses_own_position(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    await engine.tick(state(health=20.0), pathfinder(), dt=1.0)
    clock.advance(1.0)

    await engine.tick(state(health=15.0), pathfinder(), dt=1.0)

    flee = recorder.tools("flee_from")
    assert len(flee) == 1
    assert flee[0]["x"] == 100.0


async def test_escape_announce_respects_cooldown(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    await engine.tick(state(health=20.0), pathfinder(), dt=1.0)

    for health, advance in [(17.0, 1.0), (14.0, 1.0), (11.0, 30.0)]:
        clock.advance(advance)
        await engine.tick(state(health=health), pathfinder(), dt=advance)

    assert len(recorder.tools("flee_from")) == 3
    assert len(recorder.announced) == 2  # 20 秒冷却内只播报一次


async def test_is_moving_fallback_when_goal_key_missing(recorder: Recorder) -> None:
    """旧版执行器没有 goal 键时，退回 isMoving 判定。"""
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    legacy_pf = {"isMoving": True, "isBuilding": False}
    await engine.tick(state(), legacy_pf, dt=1.0)

    for _ in range(reflexes._STUCK_SECONDS):
        clock.advance(1.0)
        await engine.tick(state(), legacy_pf, dt=1.0)

    assert recorder.tools("stop_pathfinding")


async def test_frozen_pathfinding_triggers_unstuck(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    await engine.tick(state(), pathfinder(active=True), dt=1.0)

    stuck_at = None
    for _ in range(reflexes._STUCK_SECONDS):
        clock.advance(1.0)
        await engine.tick(state(), pathfinder(active=True), dt=1.0)
        if recorder.tools("stop_pathfinding"):
            stuck_at = clock.now
            break

    assert stuck_at is not None
    assert "stop_pathfinding" in [tool for tool, _ in recorder.calls]
    assert "clear_control_states" in [tool for tool, _ in recorder.calls]
    jumps = recorder.tools("set_control_state")
    assert jumps[0]["control"] == "jump" and jumps[0]["state"] is True
    assert jumps[-1]["state"] is False
    assert recorder.announced == [reflexes._STUCK_ANNOUNCE]


async def test_unstuck_grace_blocks_immediate_refire(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    await engine.tick(state(), pathfinder(active=True), dt=1.0)

    for _ in range(reflexes._STUCK_SECONDS + 2):
        clock.advance(1.0)
        await engine.tick(state(), pathfinder(active=True), dt=1.0)

    stops = recorder.tools("stop_pathfinding")
    assert len(stops) == 1  # 宽限期内不重复触发


async def test_moving_resets_frozen_accumulation(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    await engine.tick(state(x=100.0), pathfinder(active=True), dt=1.0)

    for _ in range(reflexes._STUCK_SECONDS - 2):
        clock.advance(1.0)
        await engine.tick(state(x=100.0), pathfinder(active=True), dt=1.0)
    clock.advance(1.0)
    await engine.tick(state(x=100.5), pathfinder(active=True), dt=1.0)
    for _ in range(reflexes._STUCK_SECONDS - 2):
        clock.advance(1.0)
        await engine.tick(state(x=100.5), pathfinder(active=True), dt=1.0)

    assert recorder.tools("stop_pathfinding") == []


async def test_no_goal_means_never_stuck(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    await engine.tick(state(), pathfinder(active=True), dt=1.0)

    for _ in range(reflexes._STUCK_SECONDS * 2):
        clock.advance(1.0)
        await engine.tick(state(), pathfinder(active=False), dt=1.0)

    action_tools = {tool for tool, _ in recorder.calls} - {"list_entities", "get_inventory"}
    assert action_tools == set()


@pytest.mark.parametrize("activity", [
    {"goal": True, "isMoving": False, "isMining": False, "isBuilding": False},
    {"goal": True, "isMoving": True, "isMining": True, "isBuilding": False},
    {"goal": True, "isMoving": True, "isMining": False, "isBuilding": True},
])
async def test_waiting_follow_and_terrain_work_are_not_stuck(
    recorder: Recorder, activity: dict[str, bool],
) -> None:
    engine = make_engine(recorder, FakeClock())
    for _ in range(reflexes._STUCK_SECONDS * 3):
        await engine.tick(state(), activity, dt=1)
    assert recorder.tools("stop_pathfinding") == []
    assert recorder.tools("goto") == []


async def test_flee_supersedes_stuck(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    await engine.tick(state(health=20.0), pathfinder(active=True), dt=1.0)

    health = 20.0
    for _ in range(reflexes._STUCK_SECONDS + 1):
        clock.advance(1.0)
        health -= 1.0  # 持续掉血时逃逸优先，卡死判定不被评估
        await engine.tick(state(health=health), pathfinder(active=True), dt=1.0)

    assert recorder.tools("flee_from")
    assert recorder.tools("stop_pathfinding") == []


async def test_threat_scan_failure_still_flees(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.fail_tools.add("list_entities")
    engine = make_engine(recorder, clock)
    await engine.tick(state(health=20.0), pathfinder(), dt=1.0)
    clock.advance(1.0)

    await engine.tick(state(health=18.0), pathfinder(), dt=1.0)

    assert recorder.tools("flee_from")
    assert recorder.announced


@pytest.mark.parametrize(
    "position,entities",
    [
        ("missing", []),
        ("missing", [{"name": "zombie", "distance": 3.0}]),
        ("present", []),
    ],
)
async def test_malformed_snapshots_never_raise(
    recorder: Recorder, position: str, entities: list[dict[str, Any]]
) -> None:
    clock = FakeClock()
    recorder.entities = entities
    engine = make_engine(recorder, clock)
    raw_state: dict[str, Any] = {"health": 20.0}
    if position == "present":
        raw_state["position"] = {"x": 1.0, "y": 64.0, "z": 1.0}
    else:
        raw_state["position"] = None

    await engine.tick(raw_state, pathfinder(), dt=1.0)
    clock.advance(1.0)
    raw_state["health"] = 10.0
    await engine.tick(raw_state, pathfinder(), dt=1.0)  # 不应抛出


async def test_run_loop_disconnect_resets_baseline(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    stop = reflexes.asyncio.Event()
    frames: list[dict[str, Any]] = [
        {"status": "online"},
        {"ok": True, "health": 20.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": True, "isMoving": True},
        {"status": "disconnected"},
        {"status": "online"},
        {"ok": True, "health": 10.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": True, "isMoving": True},
        {"status": "online"},
        {"ok": True, "health": 5.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": True, "isMoving": True},
    ]

    async def scripted_call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool in {"get_connection_status", "get_state", "pathfinder_status"}:
            if frames:
                return frames.pop(0)
            return {"status": "disconnected"} if tool == "get_connection_status" else {}
        return await recorder.call(tool, args)

    engine._call = scripted_call  # type: ignore[method-assign]
    task = reflexes.asyncio.create_task(engine.run(0.001, stop))
    await reflexes.asyncio.sleep(0.05)
    stop.set()
    await task

    # 断线清基线 → 重连首帧只建基线；第二帧再掉血才触发逃跑。
    # 若 dt 计算或基线复位有 bug，这里会漏发或误发 flee_from。
    assert len(recorder.tools("flee_from")) == 1


async def test_announce_failure_does_not_break_engine(recorder: Recorder) -> None:
    clock = FakeClock()

    async def broken_announce(_text: str) -> None:
        raise RuntimeError("聊天发送失败")

    engine = reflexes.ReflexEngine(recorder.call, broken_announce, clock=clock)
    await engine.tick(state(health=20.0), pathfinder(), dt=1.0)
    clock.advance(1.0)
    await engine.tick(state(health=15.0), pathfinder(), dt=1.0)

    assert recorder.tools("flee_from")  # 播报失败不影响逃跑动作


def night_state(health: float = 20.0, x: float = 100.0, z: float = 100.0) -> dict[str, Any]:
    return {
        "health": health,
        "position": {"x": x, "y": 64.0, "z": z},
        "onGround": True,
        "time": {"isDay": False},
    }


async def test_death_triggers_respawn_once_per_episode(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)

    await engine.tick({"health": None, "position": None}, pathfinder(), dt=1.0)
    await engine.tick({"health": None, "position": None}, pathfinder(), dt=1.0)

    assert len(recorder.tools("respawn")) == 1
    assert recorder.announced == [reflexes._DEATH_ANNOUNCE]

    await engine.tick(state(health=20.0), pathfinder(), dt=1.0)  # 复活收口
    clock.advance(30.0)
    await engine.tick({"health": None, "position": None}, pathfinder(), dt=1.0)

    assert len(recorder.tools("respawn")) == 2  # 新死亡 episode 再重生


async def test_night_torch_placed_at_feet(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.inventory_items = [{"name": "torch", "count": 12}]
    engine = make_engine(recorder, clock)
    await engine.tick(night_state(), pathfinder(active=False), dt=1.0)

    placed = recorder.tools("place_block")
    assert len(placed) == 1
    assert placed[0]["referenceX"] == 100.0
    assert placed[0]["referenceY"] == 63.0  # 脚下方块为参照
    assert placed[0]["faceVector"] == {"x": 0, "y": 1, "z": 0}
    assert placed[0]["itemName"] == "torch"


async def test_daytime_never_places_torch(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.inventory_items = [{"name": "torch", "count": 12}]
    engine = make_engine(recorder, clock)
    day = {**night_state(), "time": {"isDay": True}}

    for _ in range(3):
        clock.advance(reflexes._TORCH_CHECK_INTERVAL)
        await engine.tick(day, pathfinder(active=False), dt=reflexes._TORCH_CHECK_INTERVAL)

    assert recorder.tools("place_block") == []
    assert recorder.tools("get_inventory") == []  # 白天连背包都不查


async def test_no_torch_in_inventory_silently_skips(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.inventory_items = [{"name": "dirt", "count": 5}]
    engine = make_engine(recorder, clock)
    await engine.tick(night_state(), pathfinder(active=False), dt=1.0)

    assert recorder.tools("place_block") == []


async def test_torch_cooldown_limits_placement(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.inventory_items = [{"name": "torch", "count": 64}]
    engine = make_engine(recorder, clock)
    await engine.tick(night_state(), pathfinder(active=False), dt=1.0)

    clock.advance(reflexes._TORCH_CHECK_INTERVAL)  # 30s < 60s 冷却
    await engine.tick(night_state(), pathfinder(active=False), dt=reflexes._TORCH_CHECK_INTERVAL)

    assert len(recorder.tools("place_block")) == 1  # 冷却期内不重复插


async def test_busy_goal_suppresses_idle_reflexes(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.inventory_items = [{"name": "torch", "count": 12}]
    recorder.entities = [{"name": "item", "position": {"x": 101, "y": 64, "z": 100}, "distance": 1.0}]
    engine = make_engine(recorder, clock)

    for _ in range(3):
        clock.advance(reflexes._TORCH_CHECK_INTERVAL)
        await engine.tick(night_state(), pathfinder(active=True), dt=reflexes._TORCH_CHECK_INTERVAL)

    assert recorder.tools("place_block") == []
    assert recorder.tools("goto") == []


async def test_nearby_drop_triggers_pickup(recorder: Recorder) -> None:
    clock = FakeClock()
    recorder.entities = [
        {"name": "item", "position": {"x": 103, "y": 64, "z": 100}, "distance": 3.0},
        {"name": "item", "position": {"x": 101, "y": 64, "z": 100}, "distance": 1.0},
    ]
    engine = make_engine(recorder, clock)
    await engine.tick(night_state(), pathfinder(active=False), dt=1.0)

    gotos = recorder.tools("goto")
    assert len(gotos) == 1
    assert gotos[0]["x"] == 101.0  # 最近的掉落物
    assert gotos[0]["range"] == 1


async def test_pickup_guard_prevents_overlap(recorder: Recorder) -> None:
    clock = FakeClock()
    drop = {"name": "item", "position": {"x": 101, "y": 64, "z": 100}, "distance": 1.0}
    recorder.entities = [drop]
    engine = make_engine(recorder, clock)
    await engine.tick(night_state(), pathfinder(active=False), dt=1.0)

    clock.advance(reflexes._PICKUP_CHECK_INTERVAL)
    await engine.tick(night_state(), pathfinder(active=False), dt=reflexes._PICKUP_CHECK_INTERVAL)

    assert len(recorder.tools("goto")) == 1  # _picking 期间不并发捡拾


async def test_unstuck_never_cancels_active_escape(recorder: Recorder) -> None:
    """逃逸宽限期内卡死自救不得取消进行中的 flee 目标（防把 bot 钉在怪脸上）。"""
    clock = FakeClock()
    zombie = {"name": "zombie", "position": {"x": 105, "y": 64, "z": 100}, "distance": 5.0}
    recorder.entities = [zombie]
    engine = make_engine(recorder, clock)
    await engine.tick(state(health=20.0), pathfinder(active=True), dt=1.0)

    health = 20.0
    for _ in range(12):  # 持续掉血 + 位置冻结（被围），远超 8 秒卡死线
        clock.advance(1.0)
        health -= 1.0
        await engine.tick(state(health=health), pathfinder(active=True), dt=1.0)

    assert recorder.tools("flee_from")
    assert recorder.tools("stop_pathfinding") == []


async def test_autoeat_enabled_once_on_connect(recorder: Recorder) -> None:
    clock = FakeClock()
    engine = make_engine(recorder, clock)
    stop = reflexes.asyncio.Event()
    frames: list[dict[str, Any]] = [
        {"status": "online"},
        {"ok": True, "health": 20.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": False},
        {"status": "online"},
        {"ok": True, "health": 20.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": False},
        {"status": "disconnected"},
        {"status": "online"},
        {"ok": True, "health": 20.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": False},
    ]

    async def scripted_call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool in {"get_connection_status", "get_state", "pathfinder_status"}:
            return frames.pop(0) if frames else {"status": "disconnected"}
        return await recorder.call(tool, args)

    engine._call = scripted_call  # type: ignore[method-assign]
    task = reflexes.asyncio.create_task(engine.run(0.001, stop))
    await reflexes.asyncio.sleep(0.05)
    stop.set()
    await task

    enables = recorder.tools("autoeat_set_enabled")
    assert len(enables) == 2  # 首次连接 + 断线重连后各一次
    assert all(c["enabled"] is True for c in enables)
