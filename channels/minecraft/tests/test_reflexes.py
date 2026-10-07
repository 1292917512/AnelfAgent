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

    def __init__(self, entities: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.entities = entities or []
        self.announced: list[str] = []
        self.fail_tools: set[str] = set()

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, args))
        if tool in self.fail_tools:
            raise RuntimeError(f"{tool} 失败")
        if tool == "list_entities":
            return {"count": len(self.entities), "entities": self.entities}
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

    assert recorder.calls == []


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
        {"status": "connected"},
        {"ok": True, "health": 20.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": True, "isMoving": True},
        {"status": "disconnected"},
        {"status": "connected"},
        {"ok": True, "health": 10.0, "position": {"x": 0, "y": 64, "z": 0}},
        {"goal": True, "isMoving": True},
        {"status": "connected"},
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
