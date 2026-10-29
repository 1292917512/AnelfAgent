"""自主作业循环：进服开工、按组汇报、裁决继续/停止、与玩家对话并行。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest

from channels.minecraft.worker import (
    _RELOCATE_LOG_BLOCKS,
    _RELOCATE_SOIL_BLOCKS,
    MinecraftWorker,
    StackOutcome,
)
from channels.minecraft.worker_crafting import prepare_pickaxe
from channels.minecraft.worker_mining import (
    approach_shallow_face,
    blocked_tree_order,
    shallow_stone_order,
    start_stone_mining,
)


class FakeGame:
    """最小执行器面：记录调用，按脚本回放状态；钩子用于确定性时序控制。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.action: dict[str, Any] = {"active": False}
        self.state: dict[str, Any] = {"position": {"x": 10.0, "y": 64.0, "z": -5.0}}
        self.gather_results: list[dict[str, Any]] = []
        self.status_results: list[dict[str, Any]] = []
        self.gather_hooks: list = []
        self.status_hooks: list = []

    async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(args)))
        if name == "action_status":
            return dict(self.action)
        if name == "get_state":
            return dict(self.state)
        if name == "gather_resources":
            for hook in list(self.gather_hooks):
                hook()
            if self.gather_results:
                return dict(self.gather_results.pop(0))
            return {"active": True}
        if name == "gathering_status":
            for hook in list(self.status_hooks):
                hook()
            if self.status_results:
                return dict(self.status_results.pop(0))
            return {"active": False, "phase": "completed", "gained": 64, "dug": 64, "requested": 64, "reason": ""}
        return {}

    def gather_orders(self) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == "gather_resources"]

    def flip_offline_when_status_exhausted(self, flag: dict[str, bool]) -> None:
        """状态脚本耗尽的那次查询起视为掉线，让会话循环自然收尾。"""

        def hook() -> None:
            if not self.status_results:
                flag["online"] = False

        self.status_hooks.append(hook)


class ProgressionGame(FakeGame):
    """以真实库存变化驱动制作与采石的先后顺序。"""

    def __init__(self, inventory: dict[str, int]) -> None:
        super().__init__()
        self.inventory = inventory
        self.production: list[dict[str, Any]] = [
            {"id": "craft1", "item": "wooden_pickaxe", "active": True, "phase": "running"},
            {"id": "craft1", "item": "wooden_pickaxe", "active": False,
             "phase": "completed", "inventoryClean": True},
        ]
        self.deliver_pickaxe = True

    async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_inventory":
            self.calls.append((name, dict(args)))
            return {"items": [{"name": n, "count": c} for n, c in self.inventory.items()]}
        if name in {"prepare_item", "production_status"}:
            assert not self.gather_orders(), "制作尚未完成，不应开始采石"
            self.calls.append((name, dict(args)))
            status = self.production.pop(0)
            if status.get("phase") == "completed" and self.deliver_pickaxe:
                self.inventory = {"wooden_pickaxe": 1, "cobblestone": 0}
            return status
        return await super().__call__(name, args)


class FakeMind:
    def __init__(self, verdict: str = "WORK_VERDICT=continue\n泥土挖好一组，继续挖。") -> None:
        self.verdict = verdict
        self.prompts: list[str] = []

    async def reflect(self, messages: list[dict[str, str]], **_kwargs: Any) -> str:
        self.prompts.append(messages[0]["content"])
        return self.verdict


class RuntimeStub:
    def __init__(self, mind: FakeMind) -> None:
        self.mind = mind


def _make_worker(
    game: FakeGame, *, item: str = "dirt", clock: Callable[[], float] = time.monotonic,
) -> tuple[MinecraftWorker, dict[str, bool], list[str], list[str]]:
    flag = {"online": True}
    records: list[str] = []
    announces: list[str] = []

    async def record(text: str) -> None:
        records.append(text)

    async def announce(text: str) -> None:
        announces.append(text)

    worker = MinecraftWorker(
        game,
        server_id=lambda: "local",
        online=lambda: flag["online"],
        enabled=lambda: True,
        item=lambda: item,
        stack_size=lambda: 64,
        poll_seconds=lambda: 0.01,
        record=record,
        announce=announce,
        clock=clock,
    )
    return worker, flag, records, announces


async def _drain_reports(worker: MinecraftWorker) -> None:
    pending = [task for task in worker._reports if not task.done()]
    if pending:
        await asyncio.gather(*pending)


@pytest.mark.asyncio
async def test_worker_digs_without_model_and_reports_each_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = FakeGame()
    game.status_results = [
        {"active": True, "phase": "running", "gained": 30, "dug": 30, "requested": 64, "reason": ""},
        {"active": False, "phase": "completed", "gained": 64, "dug": 66, "requested": 64, "reason": ""},
    ]
    mind = FakeMind()
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: RuntimeStub(mind))
    worker, flag, records, announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)

    await asyncio.wait_for(worker._session_loop(), timeout=2)
    await _drain_reports(worker)

    orders = game.gather_orders()
    assert len(orders) == 2  # 第一组汇报还在后台进行时，第二组已受理
    assert orders[0]["block"] == "dirt"
    assert orders[0]["count"] == 64
    assert (orders[0]["x"], orders[0]["y"], orders[0]["z"]) == (10, 64, -5)
    assert orders[0]["radius"] == 8
    assert orders[0]["mode"] == "count"
    assert len(records) == 2
    assert "第 1 组泥土完成" in records[0]
    assert "64/64" in records[0]
    assert len(mind.prompts) == 2
    assert "WORK_VERDICT" in mind.prompts[0]
    assert records[0] in mind.prompts[0]
    assert len(announces) == 2
    assert "继续挖" in announces[0]
    assert not worker.holding


@pytest.mark.asyncio
async def test_repeated_same_reason_blocks_escalate_to_relocate() -> None:
    """非换区原因的同因连败升级换区：UNSAFE_ENTRY 这类不利站位靠搬家自救。"""
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)
    blocked = StackOutcome(phase="blocked", reason="GATHER_UNSAFE_ENTRY: x", dug=0, gained=0, requested=64)
    for index in range(4):
        worker._apply_block_outcome(blocked)
        assert not worker._relocate_pending, f"第 {index + 1} 次同因受阻仍在原地重试"
    worker._apply_block_outcome(blocked)
    assert worker._relocate_pending  # 第 5 次同因连败升级换区

    worker._relocate_pending = False
    worker._apply_block_outcome(StackOutcome(phase="completed", reason="", dug=1, gained=1, requested=64))
    for _ in range(4):
        worker._apply_block_outcome(blocked)
    assert not worker._relocate_pending  # 完成组已重置连败计数


@pytest.mark.asyncio
async def test_same_spot_empty_streak_relocates_regardless_of_reason() -> None:
    """失败原因轮换（寻路/挖掘中止/无目标）时，同点位空手连败也要升级换区。"""
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)
    reasons = ["GATHER_NO_TARGET: a", "Goal was changed before it could be completed!",
               "Digging aborted", "GATHER_NO_ROUTE: b", "GATHER_NO_SIGHT: c"]
    for index, reason in enumerate(reasons):
        outcome = StackOutcome(phase="blocked", reason=reason, dug=0, gained=0, requested=64,
                               entry=(10, 64, -5))
        worker._track_stuck_spot(outcome)
        if index < len(reasons) - 1:
            assert not worker._relocate_pending, "前四组仍在原地重试"
    assert worker._relocate_pending  # 第五组同点位空手，无论原因轮换都换区
    assert worker._stuck_streak == 0  # 升级后计数复位

    # 有产出或换了位置则计数归零
    worker._relocate_pending = False
    worker._track_stuck_spot(StackOutcome(phase="completed", reason="", dug=2, gained=2, requested=64,
                                          entry=(10, 64, -5)))
    assert worker._stuck_streak == 0


@pytest.mark.asyncio
async def test_worker_auto_order_flows_through_and_reports_per_item_gains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = FakeGame()
    game.status_results = [
        {"active": True, "phase": "running", "gained": 12, "dug": 14, "requested": 64, "reason": ""},
        {"active": False, "phase": "completed", "gained": 40, "dug": 43, "requested": 64, "reason": "",
         "items": [{"item": "dirt", "gained": 31, "available": 95},
                   {"item": "oak_log", "gained": 9, "available": 9}]},
    ]
    mind = FakeMind()
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: RuntimeStub(mind))
    worker, flag, records, _announces = _make_worker(game, item="auto")
    game.flip_offline_when_status_exhausted(flag)

    await asyncio.wait_for(worker._session_loop(), timeout=2)
    await _drain_reports(worker)

    orders = game.gather_orders()
    assert orders[0]["block"] == "auto"
    assert orders[0]["mode"] == "count"
    assert orders[0]["chunk"] == {"x": 0, "z": -1}  # (10,·,-5)：floor 语义负坐标落对区块
    assert "第 1 组资源完成" in records[0]
    assert "泥土 31、橡木原木 9（合计 40/64）" in records[0]
    assert "累计 泥土 31、橡木原木 9" in records[0]


@pytest.mark.asyncio
async def test_empty_blocked_groups_do_not_consume_stack_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空手受阻组不占组号：玩家看到的「第 N 组」与实际有产出的组对齐。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    blocked = {"active": False, "phase": "blocked", "gained": 0, "dug": 0, "requested": 64,
               "reason": "GATHER_NO_TARGET: none"}
    game.status_results = [dict(blocked), dict(blocked),
                           {"active": False, "phase": "completed", "gained": 64, "dug": 64,
                            "requested": 64, "reason": ""}]
    worker, flag, records, _announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)

    await asyncio.wait_for(worker._session_loop(), timeout=2)
    await _drain_reports(worker)

    assert "自主作业泥土受阻" in records[0]  # 空手受阻汇报不带组号
    assert any("第 1 组泥土完成" in r for r in records)  # 首个有产出组仍是第 1 组
    assert not any("第 3 组" in r for r in records)  # 两次空手受阻不占号


@pytest.mark.asyncio
async def test_work_goal_holds_on_inventory_holdings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有镐后补足圆石缺口，按真实持有量待命，resume 不多下一单。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class QuotaGame(FakeGame):
        def __init__(self) -> None:
            super().__init__()
            self.inventory = {"dirt": 64, "oak_log": 64, "stone_pickaxe": 1, "cobblestone": 58}

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "get_inventory":
                return {"items": [{"name": n, "count": c} for n, c in self.inventory.items()]}
            return await super().__call__(name, args)

    game = QuotaGame()
    game.status_results = [
        {"active": False, "phase": "completed", "gained": 64, "dug": 64, "requested": 64, "reason": "",
         "items": [{"item": "cobblestone", "gained": 6, "available": 64}]},
    ]
    game.gather_hooks.append(lambda: game.inventory.update(cobblestone=64))
    worker, flag, _records, announces = _make_worker(game, item="auto")
    game.flip_offline_when_status_exhausted(flag)

    task = asyncio.create_task(worker._session_loop())
    await asyncio.sleep(0.05)
    flag["online"] = False
    await asyncio.wait_for(task, timeout=3)
    await _drain_reports(worker)

    orders = game.gather_orders()
    assert len(orders) == 1
    assert orders[0]["block"] == "stone"
    assert orders[0]["count"] == 6
    assert "chunk" not in orders[0]
    assert worker.holding and worker._hold_reason == "goal_met"
    assert any("备齐" in text for text in announces)
    worker.resume()
    flag["online"] = True
    assert await worker._work_one_stack() is None
    assert len(game.gather_orders()) == 1


@pytest.mark.asyncio
async def test_work_goal_crafts_before_stone_instead_of_filling_dirt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原木已足时先制作，即使泥土不足一组，也不再下挖土订单。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class WoodMetGame(FakeGame):
        def __init__(self) -> None:
            super().__init__()
            self.inventory = {"dirt": 57, "spruce_log": 91}

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "get_inventory":
                return {"items": [{"name": n, "count": c} for n, c in self.inventory.items()]}
            if name == "prepare_item":
                self.calls.append((name, dict(args)))
                return {"id": "craft1", "item": "wooden_pickaxe", "active": True, "phase": "running"}
            if name == "production_status":
                self.calls.append((name, dict(args)))
                self.inventory = {"dirt": 57, "spruce_log": 89, "wooden_pickaxe": 1}
                return {"id": "craft1", "item": "wooden_pickaxe", "active": False,
                        "phase": "completed", "inventoryClean": True}
            return await super().__call__(name, args)

    game = WoodMetGame()
    game.status_results = [
        {"active": False, "phase": "blocked", "gained": 0, "dug": 0, "requested": 64,
         "reason": "GATHER_NO_TARGET: none", "items": []},
    ]
    worker, flag, _records, _announces = _make_worker(game, item="auto")
    game.flip_offline_when_status_exhausted(flag)

    task = asyncio.create_task(worker._session_loop())
    await asyncio.wait_for(task, timeout=3)
    await _drain_reports(worker)

    orders = game.gather_orders()
    assert orders
    assert all(order["block"] == "stone" for order in orders)
    assert all("logsOnly" not in order for order in orders)
    assert orders[0]["count"] == 3
    names = [name for name, _args in game.calls]
    assert names.index("prepare_item") < names.index("production_status") < names.index("gather_resources")
    assert not worker.holding


@pytest.mark.asyncio
@pytest.mark.parametrize("wood", [7, 8])
async def test_eight_total_logs_trigger_crafting_and_then_stone(wood: int) -> None:
    game = ProgressionGame({"dirt": 64, "oak_log": wood - 3, "birch_log": 3})
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    await worker._work_one_stack()
    order = game.gather_orders()[0]
    if wood == 7:
        assert not any(name == "prepare_item" for name, _args in game.calls)
        assert order["block"] == "auto" and order["logTarget"] == 8
        assert order["logsOnly"] is True
    else:
        assert ("prepare_item", {"item": "wooden_pickaxe", "count": 1, "mode": "ensure"}) in game.calls
        assert order["block"] == "stone" and order["count"] == 3
        await worker._work_one_stack()
        assert sum(name == "prepare_item" for name, _args in game.calls) == 1
        assert all(order["block"] == "stone" for order in game.gather_orders())
    await _drain_reports(worker)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["blocked", "dirty", "missing_pickaxe", "foreign_task", "not_started"])
async def test_unconfirmed_crafting_never_starts_stone_gathering(failure: str) -> None:
    game = ProgressionGame({"oak_log": 8})
    terminal = game.production[1]
    if failure == "blocked":
        terminal.update(phase="blocked", reason="no safe table space")
    elif failure == "dirty":
        terminal["inventoryClean"] = False
    elif failure == "missing_pickaxe":
        game.deliver_pickaxe = False
    elif failure == "foreign_task":
        terminal["id"] = "other-task"
    else:
        game.production = [{"error": "missing materials"}]
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    outcome = await worker._work_one_stack()
    assert outcome is not None and outcome.phase == "blocked"
    assert not game.gather_orders()


@pytest.mark.asyncio
@pytest.mark.parametrize("pickaxe", ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe"])
async def test_existing_pickaxe_survives_restart_and_probes_stone_only(pickaxe: str) -> None:
    game = ProgressionGame({pickaxe: 1})
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    await worker._work_one_stack()
    assert game.gather_orders()[0]["block"] == "stone"
    assert not any(name == "prepare_item" for name, _args in game.calls)
    await worker._probe_targets({"x": 0, "y": 64, "z": 0})
    probes = [args for name, args in game.calls if name == "find_blocks"]
    assert len(probes) == 1 and probes[0]["matching"] == ["stone"]


@pytest.mark.asyncio
async def test_explicit_dirt_work_does_not_enter_automatic_crafting() -> None:
    game = ProgressionGame({"oak_log": 8})
    worker, _flag, _records, _announces = _make_worker(game, item="dirt")
    await worker._work_one_stack()
    assert game.gather_orders()[0]["block"] == "dirt"
    assert not any(name == "prepare_item" for name, _args in game.calls)


@pytest.mark.asyncio
async def test_fresh_inventory_progresses_through_both_pickaxes_to_three_full_stacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """耗材真实扣库存，制作等待确认，三种物资齐备才待命。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class SurvivalGame(ProgressionGame):
        crafting: str = ""

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "prepare_item":
                self.calls.append((name, dict(args)))
                self.crafting = args["item"]
                return {"id": self.crafting, "item": self.crafting, "phase": "running", "active": True}
            if name == "production_status":
                self.calls.append((name, dict(args)))
                self.inventory[self.crafting] = 1
                self.inventory["oak_log"] -= 3 if self.crafting == "wooden_pickaxe" else 1
                if self.crafting == "stone_pickaxe":
                    self.inventory["cobblestone"] -= 3
                return {"id": self.crafting, "item": self.crafting, "phase": "completed",
                        "active": False, "inventoryClean": True}
            if name == "gather_resources":
                self.calls.append((name, dict(args)))
                if args["block"] == "stone":
                    self.inventory["cobblestone"] = self.inventory.get("cobblestone", 0) + args["count"]
                else:
                    self.inventory["dirt"] = 64
                    self.inventory["oak_log"] = args["logTarget"]
                return {"phase": "running", "active": True}
            return await super().__call__(name, args)

    game = SurvivalGame({})
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    for _ in range(8):
        if worker.holding:
            break
        assert await worker._work_one_stack() is not None
        await worker._refresh_inventory_goals()
        worker._hold_if_goal_met()
    await _drain_reports(worker)
    assert worker.holding and worker._hold_reason == "goal_met"
    assert {name: game.inventory[name] for name in ("dirt", "oak_log", "cobblestone")} == {
        "dirt": 64, "oak_log": 64, "cobblestone": 64,
    }
    assert [args["item"] for name, args in game.calls if name == "prepare_item"] == [
        "wooden_pickaxe", "stone_pickaxe",
    ]
    assert [(order["block"], order["count"]) for order in game.gather_orders()] == [
        ("auto", 64), ("stone", 3), ("stone", 64), ("auto", 64),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("pickaxe,stone,expected", [
    ("wooden_pickaxe", 0, "wooden_pickaxe"), ("stone_pickaxe", 64, "stone_pickaxe"),
])
async def test_low_durability_pickaxe_is_replaced_before_continuing(pickaxe: str, stone: int, expected: str) -> None:
    class WornGame(ProgressionGame):
        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "get_inventory":
                return {"items": [{"name": n, "count": c, "durabilityLeft": 5 if n == pickaxe else None}
                                  for n, c in self.inventory.items()]}
            if name == "prepare_item":
                self.calls.append((name, dict(args)))
                return {"error": "stop after checking replacement order"}
            return await super().__call__(name, args)

    game = WornGame({"oak_log": 64, "dirt": 64, pickaxe: 1, "cobblestone": stone})
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    outcome = await worker._work_one_stack()
    assert outcome is not None and outcome.phase == "blocked"
    assert ("prepare_item", {"item": expected, "count": 1, "mode": "craft"}) in game.calls
    assert not game.gather_orders() and not worker.holding


@pytest.mark.asyncio
async def test_buried_stone_opens_bounded_stairs_and_preserves_mining_gains() -> None:
    class ShallowGame(ProgressionGame):
        mining = False
        saved: dict[str, Any] = {"active": False, "task": None}

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "mining_status":
                self.calls.append((name, dict(args)))
                if self.mining:
                    self.inventory["cobblestone"] = 6
                    self.saved = {"id": "mine1", "active": False, "phase": "partial", "item": "cobblestone",
                                  "count": 16, "gained": 6, "dug": 10, "returned": True}
                return self.saved
            if name == "find_blocks":
                self.calls.append((name, dict(args)))
                return {"positions": [{"x": 12, "y": 62, "z": -5}]}
            if name == "mine_resources":
                self.calls.append((name, dict(args)))
                self.mining = True
                return {"id": "mine1", "active": True, "phase": "running"}
            return await super().__call__(name, args)

    game = ShallowGame({"stone_pickaxe": 1, "dirt": 64})
    game.status_results = [{"active": False, "phase": "blocked", "reason": "GATHER_NO_TARGET",
                            "gained": 0, "dug": 0, "requested": 16}]
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    outcome = await worker._work_one_stack()
    assert outcome is not None and outcome.phase == "partial"
    assert outcome.gained == 6 and outcome.dug == 10 and outcome.items == (("cobblestone", 6),)
    orders = [args for name, args in game.calls if name == "mine_resources"]
    assert orders == [{"direction": "east", "depth": 3, "length": 8, "minY": 61,
                       "item": "cobblestone", "count": 64, "extend": False, "requireTorches": False}]
    assert await shallow_stone_order(game, {"x": 10, "y": 64, "z": -5}, 10) is None
    assert len([name for name, _args in game.calls if name == "mine_resources"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stone_y,depth", [(60, 4), (58, 6), (57, None)])
async def test_starter_mine_reaches_thicker_soil_without_exceeding_depth_limit(stone_y: int, depth: int | None) -> None:
    async def call(name: str, _args: dict[str, Any]) -> dict[str, Any]:
        if name == "find_blocks":
            return {"positions": [{"x": 12, "y": stone_y, "z": -5}]}
        return {}

    order = await shallow_stone_order(call, {"x": 10, "y": 64, "z": -5}, 3)
    if depth is None:
        assert order is None
    else:
        assert order is not None and order["depth"] == depth and order["minY"] == 64 - depth


@pytest.mark.asyncio
async def test_returned_starter_mine_continues_without_repeating_surface_search() -> None:
    class CorridorGame(ProgressionGame):
        started = False

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "mining_status":
                return {"id": "mine1", "phase": "partial", "returned": True, "active": False,
                        "item": "cobblestone", "direction": "south", "depth": 4, "length": 8,
                        "steps": 12, "count": 64, "gained": 20 if self.started else 0}
            if name == "mine_resources":
                self.calls.append((name, dict(args)))
                self.started = True
                return {"active": True}
            return await super().__call__(name, args)

    game = CorridorGame({"stone_pickaxe": 1, "cobblestone": 27})
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    outcome = await worker._work_one_stack()
    assert outcome is not None and outcome.gained == 20
    assert not game.gather_orders() and not any(name == "find_blocks" for name, _args in game.calls)
    assert ("mine_resources", {"direction": "south", "depth": 4, "length": 8, "item": "cobblestone",
                               "count": 37, "extend": True, "requireTorches": False}) in game.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("returned,phase,steps,allowed", [
    (True, "partial", 3, True), (True, "partial", 2, False),
    (False, "partial", 3, False), (True, "interrupted", 3, False),
])
async def test_starter_mine_only_extends_a_fully_walked_returned_corridor(
    returned: bool, phase: str, steps: int, allowed: bool,
) -> None:
    async def call(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
        return {"id": "mine1", "returned": returned, "phase": phase, "steps": steps,
                "depth": 3, "length": 0, "item": "cobblestone", "direction": "north"}

    order = await shallow_stone_order(call, {"x": 0, "y": 63, "z": 0}, 13)
    if allowed:
        assert order == {"direction": "north", "depth": 3, "length": 8, "item": "cobblestone",
                         "count": 13, "extend": True, "requireTorches": False}
    else:
        assert order is None


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_name,visible,inside,repair", [
    ("grass_block", True, True, True), ("chest", True, True, False),
    ("grass_block", False, True, False), ("grass_block", True, False, False),
])
async def test_stone_mine_repairs_only_visible_soil_inside_its_saved_passage(
    actual_name: str, visible: bool, inside: bool, repair: bool,
) -> None:
    calls: list[str] = []
    cleared = False

    async def call(name: str, _args: dict[str, Any]) -> dict[str, Any]:
        nonlocal cleared
        calls.append(name)
        if name == "mine_resources":
            if not cleared:
                raise RuntimeError("MINING_RETURN_BLOCKED: Return passage obstructed by grass_block at -38,66,6.")
            return {"id": "mine2", "active": True}
        if name == "mining_status":
            return {"entry": {"x": -38 if inside else -48, "y": 66, "z": 6}, "direction": "north",
                    "steps": 3, "depth": 3, "returned": True, "phase": "partial"}
        if name == "get_block_at":
            return {"name": actual_name, "canSee": visible, "canDig": True}
        if name == "dig":
            cleared = True
            return {"serverConfirmed": True}
        return {}

    if repair:
        result = await start_stone_mining(call, {"extend": True})
        assert result["active"] is True
        assert calls == ["mine_resources", "mining_status", "get_block_at", "dig", "mine_resources"]
    else:
        with pytest.raises(RuntimeError, match="MINING_RETURN_BLOCKED"):
            await start_stone_mining(call, {"extend": True})
        assert "dig" not in calls


@pytest.mark.asyncio
async def test_blocking_tree_is_cleared_whole_before_same_mine_resumes() -> None:
    class TreeGame(ProgressionGame):
        cleared = False
        resumed = False

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "mining_status":
                self.calls.append((name, args))
                if self.resumed:
                    self.inventory["cobblestone"] = 16
                    return {"id": "mine1", "phase": "completed", "active": False, "item": "cobblestone",
                            "count": 13, "gained": 13, "dug": 13, "returned": True}
                return {"id": "mine1", "phase": "blocked", "returned": True, "item": "cobblestone",
                        "entry": {"x": 10, "y": 64, "z": -5}, "direction": "east", "steps": 2, "depth": 0,
                        "reason": "Unexpected birch_log at (13, 65, -5); do not excavate buildings or containers."}
            if name == "get_block_at":
                self.calls.append((name, args))
                return {"name": "air" if self.cleared else "birch_log"}
            if name == "gathering_status":
                self.calls.append((name, args))
                self.cleared = True
                return {"phase": "completed", "active": False, "gained": 5, "dug": 5, "requested": 64,
                        "items": [{"item": "birch_log", "gained": 5}]}
            if name == "resume_mining":
                self.calls.append((name, args))
                assert self.cleared
                self.resumed = True
                return {"active": True, "id": "mine1"}
            return await super().__call__(name, args)

    game = TreeGame({"stone_pickaxe": 1, "cobblestone": 3})
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    outcome = await worker._work_one_stack()
    assert game.gather_orders() == [{"block": "birch_log", "count": 64, "mode": "tree", "radius": 8,
                                    "x": 13, "y": 65, "z": -5}]
    assert outcome is not None and outcome.phase == "completed"
    assert dict(outcome.items) == {"birch_log": 5, "cobblestone": 13}
    assert await blocked_tree_order(game) is None


@pytest.mark.asyncio
async def test_shallow_mine_uses_exact_bottom_goal_instead_of_horizontal_near_goal() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, args))
        if name == "mining_status":
            return {"entry": {"x": -38, "y": 66, "z": 6}, "direction": "north", "steps": 3,
                    "depth": 3, "length": 0, "returned": True, "phase": "partial", "active": False}
        if name == "get_state":
            return {"position": {"x": -37.5, "y": 66, "z": 6.5}}
        return {}

    await approach_shallow_face(call)
    assert calls[-1] == ("goto", {"goalType": "block", "x": -38, "y": 63, "z": 3, "timeout": 30000})


@pytest.mark.asyncio
async def test_crafting_timeout_cancels_instead_of_releasing_an_active_task() -> None:
    game = ProgressionGame({"oak_log": 8})
    times = iter([0.0, 151.0])
    with pytest.raises(TimeoutError, match="CRAFT_TIMEOUT"):
        await prepare_pickaxe(game, running=lambda: True, poll_seconds=0.01, clock=lambda: next(times))
    assert ("cancel_task", {}) in game.calls
    assert not game.gather_orders()


@pytest.mark.asyncio
async def test_goal_met_hold_survives_continue_verdict() -> None:
    """配额收工持有只由玩家 !resume 解除：后台裁决 continue 不重启已达标的作业，
    否则刚宣布收工待命就自相矛盾地继续挖。"""
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)
    worker._hold_reason = "goal_met"
    worker._inv_dirt, worker._inv_wood = 130, 70

    await worker._apply_verdict("WORK_VERDICT=continue\n继续挖。")

    assert worker.holding and worker._hold_reason == "goal_met"
    assert ("resume_action", {"allowIdle": True}) not in game.calls


@pytest.mark.asyncio
async def test_worker_keeps_relocating_and_digging_without_waiting_for_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = FakeGame()
    blocked = {"active": False, "phase": "blocked", "gained": 0, "dug": 0, "requested": 64,
               "reason": "GATHER_NO_TARGET: none"}
    game.status_results = [dict(blocked), dict(blocked),
                           {"active": False, "phase": "completed", "gained": 64, "dug": 64,
                            "requested": 64, "reason": ""}]
    # 裁决通道整体不可用（供应商断连）：受阻换区自治，循环绝不等模型。
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    worker, flag, records, _announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)

    await asyncio.wait_for(worker._session_loop(), timeout=2)
    await _drain_reports(worker)

    # 受阻两组各自换区后继续；脚本耗尽后 default completed 再收一组（FakeGame 工件）。
    assert len(game.gather_orders()) == 4
    assert not worker.holding  # 受阻从不进入持有；模型不回应等价于 continue
    gotos = _goto_orders(game)
    assert [(g["x"], g["z"]) for g in gotos] == [(18, -5), (10, 43)]  # 首次受阻近距换区（东 8 格）；二次受阻无产出即远程跳区（南 48 格）
    assert any("受阻" in record for record in records)


@pytest.mark.asyncio
async def test_stop_verdict_pauses_executor_and_hold_blocks_next_stack() -> None:
    game = FakeGame()
    game.action = {"active": True, "phase": "running"}
    worker, _flag, _records, announces = _make_worker(game)

    await worker._apply_verdict("WORK_VERDICT=stop\n好的，我先停。")
    assert worker.holding
    assert ("pause_action", {}) in game.calls
    assert any("先停" in text for text in announces)

    game.action = {"active": False}
    await worker._apply_verdict("WORK_VERDICT=continue\n继续挖。")
    assert not worker.holding
    assert ("resume_action", {"allowIdle": True}) in game.calls


@pytest.mark.asyncio
async def test_stop_verdict_during_watch_drops_stack_without_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = FakeGame()
    game.status_results = [{"active": True, "phase": "running", "gained": 10, "dug": 10,
                            "requested": 64, "reason": ""}]
    worker, flag, records, _announces = _make_worker(game)

    def stop_mid_dig() -> None:
        worker._hold_reason = "model_stop"
        flag["online"] = False

    game.status_hooks.append(stop_mid_dig)
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    await asyncio.wait_for(worker._session_loop(), timeout=2)

    assert len(game.gather_orders()) == 1
    assert records == []  # 裁决落在挖掘中途：放弃记账，不产生汇报


@pytest.mark.asyncio
async def test_worker_waits_for_foreign_action_before_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    game.action = {"active": True, "phase": "running"}
    worker, flag, _records, _announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)
    gather_times: list[float] = []
    game.gather_hooks.append(lambda: gather_times.append(time.monotonic()))
    release_at = time.monotonic()

    async def release() -> None:
        await asyncio.sleep(0.05)
        game.action = {"active": False}

    releaser = asyncio.create_task(release())
    await asyncio.wait_for(worker._session_loop(), timeout=2)
    await releaser

    assert gather_times and gather_times[0] >= release_at  # 他方动作释放后才开工


@pytest.mark.asyncio
async def test_pause_suppresses_and_resume_restores_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)

    worker.tick("online")
    first = worker._session
    assert first is not None
    worker.pause()
    await asyncio.gather(first, return_exceptions=True)
    await asyncio.sleep(0)

    worker.tick("online")
    assert worker._session is None  # 手动暂停期间不开工

    worker.resume()
    worker.tick("online")
    assert worker._session is not None
    await worker.stop()


@pytest.mark.asyncio
async def test_offline_tick_never_starts_session() -> None:
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)

    worker.tick(None)
    worker.tick("offline")
    assert worker._session is None


def _goto_orders(game: FakeGame) -> list[dict[str, Any]]:
    return [args for name, args in game.calls if name == "goto"]


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_no_target_continue_verdict_arms_relocation() -> None:
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)

    worker._hold_reason = "GATHER_NO_TARGET: none"
    worker._last_block_reason = "GATHER_NO_TARGET: none"
    await worker._apply_verdict("WORK_VERDICT=continue\n继续挖。")
    assert worker._relocate_pending  # 无目标持有被裁决解除后，下一组开工前先换区
    assert not worker.holding
    assert _goto_orders(game) == []  # 裁决本身不移动 bot，换区发生在下一轮开工前

    worker._relocate_pending = False  # 模拟上一轮已消费换区
    worker._hold_reason = "GATHER_TIMEOUT: budget exhausted"
    worker._last_block_reason = "GATHER_TIMEOUT: budget exhausted"
    await worker._apply_verdict("WORK_VERDICT=continue\n继续挖。")
    assert not worker._relocate_pending  # 非无目标受阻不触发换区


@pytest.mark.asyncio
async def test_relocate_pending_moves_bot_before_next_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    worker, flag, _records, _announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)

    worker._relocate_pending = True
    await asyncio.wait_for(worker._session_loop(), timeout=2)

    gotos = _goto_orders(game)
    assert len(gotos) == 1
    # 出生点 (10, 64, -5) 朝第一个轮换方向（东）先走近距 8 格即达；
    # y 无关目标（nearxz）适配起伏地形，固定高度的 near 目标会大量不可达。
    assert (gotos[0]["x"], gotos[0]["z"]) == (18, -5)
    assert gotos[0]["goalType"] == "nearxz"
    assert "y" not in gotos[0]
    sequence = [name for name, _args in game.calls]
    assert sequence.index("goto") < sequence.index("gather_resources")
    assert len(game.gather_orders()) == 1


@pytest.mark.asyncio
async def test_relocate_failure_rotates_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    """单方向寻路失败时轮换下一方向重试，不再半途放弃导致原地开工。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class FlakyGotoGame(FakeGame):
        def __init__(self) -> None:
            super().__init__()
            self.goto_failures = 1

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "goto" and self.goto_failures > 0:
                self.goto_failures -= 1
                self.calls.append((name, dict(args)))
                raise RuntimeError("MCP 工具 'goto' 执行失败: Error [NO_PATH]")
            return await super().__call__(name, args)

    game = FlakyGotoGame()
    worker, flag, _records, _announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)

    worker._relocate_pending = True
    await asyncio.wait_for(worker._session_loop(), timeout=2)

    gotos = _goto_orders(game)
    assert len(gotos) == 2  # 近距失败后立即改试远距
    # 出生点 (10, 64, -5)：东近 (18, -5) 失败，改东远 (34, -5) 成功
    assert (gotos[0]["x"], gotos[0]["z"]) == (18, -5)
    assert (gotos[1]["x"], gotos[0]["z"]) != gotos[1]["x"] or (gotos[1]["x"], gotos[1]["z"]) == (34, -5)
    assert len(game.gather_orders()) == 1


@pytest.mark.asyncio
async def test_relocate_walks_toward_nearest_visible_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """可见资源优先于盲走：探测到最近的泥土/原木就直接走过去。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class ProbeGame(FakeGame):
        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "find_blocks":
                return {"positions": [
                    {"x": 12, "y": 60, "z": -30},
                    {"x": -40, "y": 64, "z": 90},
                    {"x": 10, "y": 20, "z": -5},
                ]}
            return await super().__call__(name, args)

    game = ProbeGame()
    worker, _flag, _records, _announces = _make_worker(game)

    await worker._relocate()

    gotos = _goto_orders(game)
    assert len(gotos) == 1
    assert gotos[0]["goalType"] == "nearxz"
    assert (gotos[0]["x"], gotos[0]["z"]) == (12, -30)
    assert worker._last_relocate_target == (12, -30)

    # 刚走完的目标不重走：下一轮跳过同一片区域，改走向次近的可见资源。
    await worker._relocate()
    gotos = _goto_orders(game)
    assert len(gotos) == 2
    assert (gotos[1]["x"], gotos[1]["z"]) == (-40, 90)


@pytest.mark.asyncio
async def test_relocate_probe_prefers_reserve_soil_and_orders_by_chunk_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """背包泥土低于保底时泥土命中优先；保底满足后按「区块环距 → 块距」扩搜。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class RingProbeGame(FakeGame):
        def __init__(self, dirt: int) -> None:
            super().__init__()
            self._dirt = dirt

        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "get_inventory":
                return {"items": [{"name": "dirt", "count": self._dirt}]}
            if name == "find_blocks":
                if set(args["matching"]) == set(_RELOCATE_SOIL_BLOCKS):
                    return {"positions": [{"x": 40, "y": 64, "z": 0}]}
                assert set(args["matching"]) == set(_RELOCATE_LOG_BLOCKS)
                return {"positions": [{"x": 20, "y": 64, "z": 0}, {"x": 12, "y": 64, "z": 0}]}
            return await super().__call__(name, args)

    here = {"x": 10, "y": 64, "z": -5}  # 所在区块 (0, -1)

    game = RingProbeGame(dirt=5)
    worker, _flag, _records, _announces = _make_worker(game)
    targets = await worker._probe_targets(here)
    assert [(t["x"], t["z"]) for t in targets] == [(40, 0), (12, 0), (20, 0)], (
        "泥土（环 2）在保底未满足时排在更近的原木（环 1）之前"
    )

    game = RingProbeGame(dirt=20)
    worker, _flag, _records, _announces = _make_worker(game)
    targets = await worker._probe_targets(here)
    assert [(t["x"], t["z"]) for t in targets] == [(12, 0), (20, 0), (40, 0)], (
        "保底满足后回到环距+块距的就近扩搜"
    )

    game = RingProbeGame(dirt=64)
    worker, _flag, _records, _announces = _make_worker(game, item="auto")
    targets = await worker._probe_targets(here)
    assert [(t["x"], t["z"]) for t in targets] == [(12, 0), (20, 0)]
    assert all(set(args["matching"]) != set(_RELOCATE_SOIL_BLOCKS)
               for name, args in game.calls if name == "find_blocks")


@pytest.mark.asyncio
async def test_consecutive_dead_relocations_escalate_to_long_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """近距换区连续无产出：跳过探测，直接远程跳区离开残破地带。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)
    worker._relocate_fails = 2  # 前两轮换区都没换来产出

    async def empty_probe(here: dict[str, int]) -> list[dict[str, Any]]:
        return []  # 无下方目标可下撤，也无可见资源可走向

    worker._probe_targets = empty_probe
    await worker._relocate()

    gotos = _goto_orders(game)
    assert len(gotos) == 1
    assert gotos[0]["goalType"] == "nearxz"
    # 出生点 (10,·,-5) 朝东 48 格
    assert (gotos[0]["x"], gotos[0]["z"]) == (58, -5)


@pytest.mark.asyncio
async def test_no_target_relocations_naturally_escalate_to_long_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_TARGET 换区循环自身计入无产出计数：探测命中互相指向残破区内部
    （可见但过不了安全检查）时，空转两轮即远程跳区，无需其他升级路径。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)

    blocked = StackOutcome(phase="blocked", reason="GATHER_NO_TARGET: none", dug=0, gained=0, requested=64)
    worker._apply_block_outcome(blocked)
    worker._apply_block_outcome(blocked)
    assert worker._relocate_fails == 2
    assert worker._relocate_pending

    async def empty_probe(here: dict[str, int]) -> list[dict[str, Any]]:
        return []  # 无下方目标可下撤，也无可见资源可走向

    worker._probe_targets = empty_probe
    await worker._relocate()
    gotos = _goto_orders(game)
    assert len(gotos) == 1
    assert (gotos[0]["x"], gotos[0]["z"]) == (58, -5)


@pytest.mark.asyncio
async def test_escalated_escape_exhaustion_cools_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """升级跳区四向全不通（山脊地形长距 NO_PATH）：进入冷却播报，绝不落回 8 格碎步。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class NoPathGame(FakeGame):
        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "goto":
                self.calls.append((name, dict(args)))
                raise RuntimeError("MCP 工具 'goto' 执行失败: Error [NO_PATH]")
            return await super().__call__(name, args)

    game = NoPathGame()
    worker, _flag, _records, announces = _make_worker(game, clock=lambda: 100.0)
    worker._relocate_fails = 2

    async def empty_probe(here: dict[str, int]) -> list[dict[str, Any]]:
        return []  # 无下方目标可下撤，也无可见资源可走向

    worker._probe_targets = empty_probe
    await worker._relocate()

    gotos = _goto_orders(game)
    # 四个方向 × (48, 24) 各试一次，全部 NO_PATH 后进入冷却
    assert len(gotos) == 8
    assert all(g["goalType"] == "nearxz" for g in gotos)
    assert worker._relocate_cooldown_until > 0
    assert any("走不出去" in text for text in announces)


@pytest.mark.asyncio
async def test_escalated_relocate_descends_to_resource_below_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """升级换区前先查脚下：站在树冠/悬空树叶上时四向寻路全是 NO_PATH，
    探测命中的下方资源才是唯一可达出路——直接受控下撤，不做任何跳区。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)
    worker._relocate_fails = 2

    async def below_probe(here: dict[str, int]) -> list[dict[str, Any]]:
        # 最近命中是同层目标，正下方的泥土排在后面：下撤检查必须扫全表
        return [{"x": here["x"] + 2, "y": here["y"], "z": here["z"]},
                {"x": here["x"], "y": here["y"] - 9, "z": here["z"]}]

    descended: list[int | None] = []

    async def fake_descend(target_y: int | None = None) -> bool:
        descended.append(target_y)
        return True

    worker._probe_targets = below_probe
    worker._descend = fake_descend
    await worker._relocate()

    assert descended == [64 - 9]  # FakeGame 出生点 y=64，目标在其下 9 格
    assert _goto_orders(game) == []  # 下撤成功即完成换区，绝不寻路


@pytest.mark.asyncio
@pytest.mark.parametrize("started_at", [0.0, 1000.0])
async def test_cooldown_announcements_are_throttled(
    monkeypatch: pytest.MonkeyPatch, started_at: float,
) -> None:
    """首次求助立即播报，后续按实际播报时间节流，满 5 分钟恢复。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class NoPathGame(FakeGame):
        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "goto":
                self.calls.append((name, dict(args)))
                raise RuntimeError("MCP 工具 'goto' 执行失败: Error [NO_PATH]")
            return await super().__call__(name, args)

    game = NoPathGame()
    now = started_at
    worker, _flag, _records, announces = _make_worker(game, clock=lambda: now)
    worker._relocate_fails = 2

    async def empty_probe(here: dict[str, int]) -> list[dict[str, Any]]:
        return []

    worker._probe_targets = empty_probe
    await worker._relocate()
    assert len([text for text in announces if "走不出去" in text]) == 1

    for elapsed in (60.0, 299.0):
        now = started_at + elapsed
        await worker._relocate()
        assert len([text for text in announces if "走不出去" in text]) == 1

    now = started_at + 300.0
    await worker._relocate()
    assert len([text for text in announces if "走不出去" in text]) == 2


@pytest.mark.asyncio
async def test_relocate_total_failure_cools_down_and_announces(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测与四个方向全部失败：进入冷却不再打转，并播报一次告知玩家。"""
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)

    class NoPathGame(FakeGame):
        async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "goto":
                self.calls.append((name, dict(args)))
                raise RuntimeError("MCP 工具 'goto' 执行失败: Error [NO_PATH]")
            return await super().__call__(name, args)

    game = NoPathGame()
    worker, flag, _records, announces = _make_worker(game, clock=lambda: 100.0)

    await worker._relocate()

    assert len(_goto_orders(game)) == 8  # 无可见资源，四个方向近远各试一次
    assert worker._relocate_cooldown_until > worker._clock()
    assert any("歇" in text for text in announces)

    worker._relocate_pending = True
    task = asyncio.create_task(worker._session_loop())
    await asyncio.sleep(0.05)
    flag["online"] = False
    await asyncio.wait_for(task, timeout=2)
    assert len(_goto_orders(game)) == 8  # 冷却期内不再重试换区
    assert game.gather_orders() == []


@pytest.mark.asyncio
async def test_no_target_block_relocates_immediately_without_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    blocked = {"active": False, "phase": "blocked", "gained": 0, "dug": 0, "requested": 64,
               "reason": "GATHER_NO_TARGET: none"}
    game.status_results = [dict(blocked), dict(blocked)]
    worker, flag, _records, _announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)

    # 无目标受阻当场换区，绝不持有：循环自主跑完，不需要裁决或手动 resume。
    await asyncio.wait_for(worker._session_loop(), timeout=2)
    await _drain_reports(worker)

    assert not worker.holding
    assert not worker._relocate_pending  # 两次受阻均已当场换区消费
    gotos = _goto_orders(game)
    assert [(g["x"], g["z"]) for g in gotos] == [(18, -5), (10, 43)]  # 首次受阻近距换区（东 8 格）；二次受阻无产出即远程跳区（南 48 格）
    await worker.stop()


@pytest.mark.asyncio
async def test_non_no_target_hold_does_not_relocate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: None)
    game = FakeGame()
    worker, flag, _records, _announces = _make_worker(game)
    game.flip_offline_when_status_exhausted(flag)

    worker._hold_reason = "GATHER_TIMEOUT: budget exhausted"
    worker._last_block_reason = "GATHER_TIMEOUT: budget exhausted"
    await worker._apply_verdict("WORK_VERDICT=continue\n继续挖。")
    assert not worker._relocate_pending

    await asyncio.wait_for(worker._session_loop(), timeout=2)
    assert _goto_orders(game) == []  # 非无目标受阻不换区，原地继续
    assert len(game.gather_orders()) == 1


@pytest.mark.asyncio
async def test_verdict_turn_times_out_to_continue_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """裁决快车道整轮限时：供应商链卡死到点放弃，等价 continue，不持有不播报。"""

    class StuckMind(FakeMind):
        async def reflect(self, messages: list[dict[str, str]], **_kwargs: Any) -> str:
            self.prompts.append(messages[0]["content"])
            await asyncio.sleep(5)
            return self.verdict

    mind = StuckMind()
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: RuntimeStub(mind))
    monkeypatch.setattr("channels.minecraft.worker._VERDICT_TIMEOUT_SECONDS", 0.05)
    game = FakeGame()
    worker, _flag, _records, announces = _make_worker(game)

    await asyncio.wait_for(worker._verdict_turn("第 1 组汇报"), timeout=2)

    assert len(mind.prompts) == 1  # reflect 已发起，超时由墙钟上限强制收口
    assert not worker.holding  # 超时等价 continue
    assert announces == []


@pytest.mark.asyncio
async def test_verdict_reflect_runs_under_off_recall_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """裁决快车道声明：reflect 执行期间召回策略被钉在 off 档（SDK 直调入口策略面）。"""
    from agent.memory.recall_policy import current_recall_policy

    seen: list[str] = []

    class ProbingMind(FakeMind):
        async def reflect(self, messages: list[dict[str, str]], **_kwargs: Any) -> str:
            seen.append(current_recall_policy().level)
            return await super().reflect(messages, **_kwargs)

    mind = ProbingMind()
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: RuntimeStub(mind))
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)

    await worker._verdict_turn("第 1 组汇报")
    assert seen == ["off"]


def test_verdict_message_dedupes_repeated_lines() -> None:
    """多轮迭代复述同一句播报时按行去重，裁决标记行剥除保持不变。"""
    from channels.minecraft.worker import _verdict_message

    text = (
        "WORK_VERDICT=continue\n已收集128个泥土，继续下一组。\n"
        "WORK_VERDICT=continue\n已收集128个泥土，继续下一组。"
    )
    assert _verdict_message(text) == "已收集128个泥土，继续下一组。"


@pytest.mark.asyncio
async def test_no_route_block_relocates_like_no_target() -> None:
    """支架路线不可达（NO_ROUTE）与无目标同通道：换区绕开，绝不持有。"""
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)

    worker._apply_block_outcome(StackOutcome(
        phase="blocked", reason="GATHER_NO_ROUTE: No confirmed scaffold route.",
        dug=0, gained=0, requested=64))
    assert worker._relocate_pending
    assert not worker.holding


@pytest.mark.asyncio
async def test_rapid_same_reason_blocks_suppress_report_storm() -> None:
    """秒级同因连败：首次必报，重复抑制汇报——裁决旁路不能被打成 LLM 风暴。"""
    game = FakeGame()
    worker, _flag, _records, _announces = _make_worker(game)
    outcome = StackOutcome(
        phase="blocked", reason="GATHER_NO_ROUTE: No confirmed scaffold route.",
        dug=0, gained=0, requested=64)

    worker._apply_block_outcome(outcome)
    assert not worker._suppress_report  # 首次必报

    worker._apply_block_outcome(outcome)  # 15 秒窗口内同因重复
    assert worker._suppress_report
    assert not worker.holding  # 风暴也只是自治继续，绝不持有

    worker._apply_block_outcome(StackOutcome(
        phase="completed", reason="", dug=64, gained=64, requested=64))
    assert not worker._suppress_report  # 完成即复位

    worker._apply_block_outcome(outcome)  # 新一轮受阻首报不受上一轮抑制
    assert not worker._suppress_report
