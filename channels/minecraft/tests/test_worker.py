"""自主作业循环：进服开工、按组汇报、裁决继续/停止、与玩家对话并行。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from channels.minecraft.worker import MinecraftWorker, StackOutcome


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


def _make_worker(game: FakeGame) -> tuple[MinecraftWorker, dict[str, bool], list[str], list[str]]:
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
        item=lambda: "dirt",
        stack_size=lambda: 64,
        poll_seconds=lambda: 0.01,
        record=record,
        announce=announce,
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
    assert [(g["x"], g["y"], g["z"]) for g in gotos] == [(34, 64, -5), (10, 64, 19)]  # 四方向轮转换区
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
    # 出生点 (10, 64, -5) 朝第一个轮换方向（东）前进 24 格后再开工
    assert (gotos[0]["x"], gotos[0]["y"], gotos[0]["z"]) == (34, 64, -5)
    assert gotos[0]["goalType"] == "near"
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
    assert len(gotos) == 2  # 东向 NoPath 失败后立即轮换到南向
    # 出生点 (10, 64, -5)：东 (34, -5) 失败，改南 (10, 19)
    assert (gotos[0]["x"], gotos[0]["z"]) == (34, -5)
    assert (gotos[1]["x"], gotos[1]["z"]) == (10, 19)
    assert len(game.gather_orders()) == 1


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
    assert [(g["x"], g["y"], g["z"]) for g in gotos] == [(34, 64, -5), (10, 64, 19)]
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
