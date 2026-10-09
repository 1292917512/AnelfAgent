from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from channels.minecraft.lifestyle import MinecraftLifestyle
from channels.minecraft.world_plan import WorldPlan, WorldPlanStore


@pytest.mark.asyncio
async def test_lifestyle_waits_for_idle_and_runs_one_reflection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class Mind:
        async def reflect(self, messages, **kwargs):
            calls.append({"messages": messages, **kwargs})
            return "done"

    monkeypatch.setattr(
        "agent.runtime.singleton.get_runtime",
        lambda: SimpleNamespace(mind=Mind()),
    )
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "local",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
    )
    lifestyle._last_player_activity -= 31
    lifestyle.tick("online")
    await asyncio.sleep(0)
    await lifestyle.stop()

    assert len(calls) == 1
    assert calls[0]["adapter_key"] == "minecraft"
    assert calls[0]["tool_tags"] == ["mcp:minecraft"]
    assert calls[0]["allow_output_tools"] is False
    content = calls[0]["messages"][0]["content"]
    assert "move_to_position" in content
    assert "goalType=near" in content
    assert "range=2" in content
    assert "z increases toward south" in content
    assert "south wall is z+6" in content
    assert "TEMPLATE_VERIFIED=true" in content
    assert "world_signature" in content
    assert "one concrete safe action" in content
    assert "home_phase" in content
    assert "prepare_item(mode=ensure,count=1)" in content
    assert "first use get_path_to to verify a safe route" in content
    assert "never pass a remote tree coordinate directly" in content
    assert "first search for logs within 8 blocks" in content
    assert "If get_path_to or goto returns NO_PATH" in content
    assert "reference is the existing support block, not the target" in content
    assert "find_build_site" in content
    assert "prepare_build_site" in content
    assert "Never invent home coordinates" in content
    assert "including walls, roof, and interior" in content
    assert "build_starter_cabin" in content
    assert "never accepts dirt as structural material" in content


@pytest.mark.asyncio
async def test_lifestyle_pause_blocks_new_round_until_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Mind:
        async def reflect(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return "done"

    monkeypatch.setattr(
        "agent.runtime.singleton.get_runtime",
        lambda: SimpleNamespace(mind=Mind()),
    )
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "local",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
    )
    lifestyle._last_player_activity -= 31
    lifestyle.pause()
    lifestyle.tick("online")
    await asyncio.sleep(0)
    assert calls == 0

    lifestyle.resume()
    lifestyle._last_player_activity -= 31
    lifestyle._next_run = 0
    lifestyle.tick("online")
    await asyncio.sleep(0)
    await lifestyle.stop()
    assert calls == 1


def test_world_plan_store_round_trips_and_isolates_worlds(tmp_path) -> None:
    store = WorldPlanStore(tmp_path)
    plan = WorldPlan.from_marker(
        "world-a",
        {
            "home": {"x": 1, "y": 64, "z": 2},
            "mine_entrance": {"x": 7, "y": 64, "z": 2},
            "mine_direction": "east",
            "terrain": "plains_like",
            "home_template": "starter_cabin_v1",
        },
    )

    store.save(plan)

    assert store.load("world-a") == plan
    assert plan.home_phase == "foundation"
    assert store.load("world-b") is None


def test_world_plan_store_ignores_corrupt_data(tmp_path) -> None:
    store = WorldPlanStore(tmp_path)
    digest = hashlib.sha256(b"bad").hexdigest()
    (tmp_path / f"{digest}.json").write_text("{}", encoding="utf-8")

    assert store.load("bad") is None


def test_plan_marker_preserves_existing_home_phase(tmp_path) -> None:
    store = WorldPlanStore(tmp_path)
    current = WorldPlan.from_marker(
        "world-a",
        {
            "home": {"x": 1, "y": 64, "z": 2},
            "mine_entrance": {"x": 1, "y": 64, "z": 8},
            "mine_direction": "south",
            "terrain": "plains_like",
            "home_template": "starter_cabin_v1",
            "home_phase": "walls",
            "activity_index": 4,
        },
    )
    store.save(current)
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
        plan_store=store,
    )

    lifestyle._save_plan_marker(
        "world-a",
        'WORLD_PLAN={"home":{"x":1,"y":64,"z":2},'
        '"mine_entrance":{"x":1,"y":64,"z":8},'
        '"mine_direction":"south","terrain":"plains_like",'
        '"home_template":"starter_cabin_v1"}',
    )

    saved = store.load("world-a")
    assert saved is not None
    assert saved.home_phase == "walls"
    assert saved.activity_index == 4


def test_plan_marker_rejects_unverified_phase_advance(tmp_path) -> None:
    store = WorldPlanStore(tmp_path)
    current = WorldPlan.from_marker(
        "world-a",
        {
            "home": {"x": 1, "y": 64, "z": 2},
            "mine_entrance": {"x": 1, "y": 64, "z": 8},
            "mine_direction": "south",
            "terrain": "plains_like",
            "home_template": "starter_cabin_v1",
            "home_phase": "walls",
        },
    )
    store.save(current)
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
        plan_store=store,
    )

    lifestyle._save_plan_marker(
        "world-a",
        'WORLD_PLAN={"home":{"x":1,"y":64,"z":2},'
        '"mine_entrance":{"x":1,"y":64,"z":8},'
        '"mine_direction":"south","terrain":"plains_like",'
        '"home_template":"starter_cabin_v1","home_phase":"roof"}',
    )

    saved = store.load("world-a")
    assert saved is not None
    assert saved.home_phase == "walls"


def test_plan_marker_accepts_verified_phase_advance(tmp_path) -> None:
    store = WorldPlanStore(tmp_path)
    store.save(
        WorldPlan.from_marker(
            "world-a",
            {
                "home": {"x": 1, "y": 64, "z": 2},
                "mine_entrance": {"x": 1, "y": 64, "z": 8},
                "mine_direction": "south",
                "terrain": "plains_like",
                "home_template": "starter_cabin_v1",
                "home_phase": "walls",
            },
        ),
    )
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
        plan_store=store,
    )

    lifestyle._save_plan_marker(
        "world-a",
        "TEMPLATE_VERIFIED=true\n"
        'WORLD_PLAN={"home":{"x":1,"y":64,"z":2},'
        '"mine_entrance":{"x":1,"y":64,"z":8},'
        '"mine_direction":"south","terrain":"plains_like",'
        '"home_template":"starter_cabin_v1","home_phase":"roof"}',
    )

    saved = store.load("world-a")
    assert saved is not None
    assert saved.home_phase == "roof"


def test_plan_marker_records_runtime_world_signature(tmp_path) -> None:
    store = WorldPlanStore(tmp_path)
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a",
        world_signature=lambda: "127.0.0.1:7850:AnelfBot:overworld",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
        plan_store=store,
    )

    lifestyle._save_plan_marker(
        "world-a",
        'WORLD_PLAN={"home":{"x":1,"y":64,"z":2},'
        '"mine_entrance":{"x":1,"y":64,"z":8},'
        '"mine_direction":"south","terrain":"plains_like",'
        '"home_template":"starter_cabin_v1"}',
    )

    saved = store.load("world-a")
    assert saved is not None
    assert saved.world_signature == "127.0.0.1:7850:AnelfBot:overworld"


@pytest.mark.asyncio
async def test_lifestyle_replans_when_world_signature_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    prompts: list[str] = []

    class Mind:
        async def reflect(self, messages, **_kwargs):
            prompts.append(messages[0]["content"])
            return ""

    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: SimpleNamespace(mind=Mind()))
    store = WorldPlanStore(tmp_path)
    store.save(
        WorldPlan.from_marker(
            "world-a",
            {
                "world_signature": "127.0.0.1:7850:AnelfBot:overworld",
                "home": {"x": 1, "y": 64, "z": 2},
                "mine_entrance": {"x": 1, "y": 64, "z": 8},
                "mine_direction": "south",
                "terrain": "plains_like",
                "home_template": "starter_cabin_v1",
            },
        ),
    )
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a",
        world_signature=lambda: "127.0.0.1:7327:AnelfBot:overworld",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
        plan_store=store,
    )
    lifestyle._last_player_activity -= 31
    lifestyle.tick("online")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await lifestyle.stop()

    assert prompts
    assert "base_planning" in prompts[0]


@pytest.mark.asyncio
async def test_lifestyle_advances_focus_after_an_empty_reflection(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    class Mind:
        async def reflect(self, *_args, **_kwargs):
            return ""

    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: SimpleNamespace(mind=Mind()))
    store = WorldPlanStore(tmp_path)
    store.save(
        WorldPlan.from_marker(
            "world-a",
            {
                "home": {"x": 1, "y": 64, "z": 2},
                "mine_entrance": {"x": 1, "y": 64, "z": 8},
                "mine_direction": "south",
                "terrain": "plains_like",
                "home_template": "starter_cabin_v1",
                "activity_index": 2,
            },
        ),
    )
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
        plan_store=store,
    )
    lifestyle._last_player_activity -= 31
    lifestyle.tick("online")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await lifestyle.stop()

    saved = store.load("world-a")
    assert saved is not None
    assert saved.activity_index == 3


@pytest.mark.asyncio
async def test_lifestyle_persists_world_plan_marker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class Mind:
        async def reflect(self, *_args, **_kwargs):
            return ('WORLD_PLAN={"home":{"x":1,"y":64,"z":2},'
                    '"mine_entrance":{"x":1,"y":64,"z":8},'
                    '"mine_direction":"south","terrain":"plains_like",'
                    '"home_template":"starter_cabin_v1"}')

    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: SimpleNamespace(mind=Mind()))
    store = WorldPlanStore(tmp_path)
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}),
        lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a",
        enabled=lambda: True,
        interval_seconds=lambda: 60,
        idle_seconds=lambda: 30,
        plan_store=store,
    )
    lifestyle._last_player_activity -= 31
    lifestyle.tick("online")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await lifestyle.stop()

    plan = store.load("world-a")
    assert plan is not None
    assert plan.mine_direction == "south"


@pytest.mark.asyncio
async def test_lifestyle_preserves_runtime_activity_index_from_stale_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    class Mind:
        async def reflect(self, *_args, **_kwargs):
            return ('WORLD_PLAN={"home":{"x":1,"y":64,"z":2},'
                    '"mine_entrance":{"x":1,"y":64,"z":8},'
                    '"mine_direction":"south","terrain":"plains_like",'
                    '"home_template":"starter_cabin_v1","activity_index":0}')

    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: SimpleNamespace(mind=Mind()))
    store = WorldPlanStore(tmp_path)
    store.save(WorldPlan.from_marker("world-a", {
        "home": {"x": 1, "y": 64, "z": 2},
        "mine_entrance": {"x": 1, "y": 64, "z": 8},
        "mine_direction": "south", "terrain": "plains_like",
        "home_template": "starter_cabin_v1", "activity_index": 4,
    }))
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}), lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a", enabled=lambda: True,
        interval_seconds=lambda: 60, idle_seconds=lambda: 30, plan_store=store,
    )
    lifestyle._last_player_activity -= 31
    lifestyle.tick("online")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await lifestyle.stop()

    saved = store.load("world-a")
    assert saved is not None
    assert saved.activity_index == 5


@pytest.mark.asyncio
async def test_lifestyle_prioritizes_equipment_after_mining_supply_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    class Mind:
        async def reflect(self, *_args, **_kwargs):
            return "mine_resources rejected: MINING_LOW_SUPPLIES"

    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: SimpleNamespace(mind=Mind()))
    store = WorldPlanStore(tmp_path)
    store.save(WorldPlan.from_marker("world-a", {
        "home": {"x": 1, "y": 64, "z": 2},
        "mine_entrance": {"x": 1, "y": 64, "z": 8},
        "mine_direction": "south", "terrain": "plains_like",
        "home_template": "starter_cabin_v1", "activity_index": 4,
    }))
    lifestyle = MinecraftLifestyle(
        lambda *_: asyncio.sleep(0, result={}), lambda _: asyncio.sleep(0),
        server_id=lambda: "world-a", enabled=lambda: True,
        interval_seconds=lambda: 60, idle_seconds=lambda: 30, plan_store=store,
    )
    lifestyle._last_player_activity -= 31
    lifestyle.tick("online")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await lifestyle.stop()

    saved = store.load("world-a")
    assert saved is not None
    assert saved.activity_index == 2
