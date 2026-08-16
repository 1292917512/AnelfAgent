"""对话摘要窗口单元测试：水位线取数 / 折叠触发 / 折叠执行 / 失败降级。

窗口语义：固定摘要块（水位线之前）+ 原始窗口（水位线之后纯追加，
x 条起增长到 M 触发折叠，最旧 M-x 条并入摘要后窗口重置为 x）。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent.storage import conversation_fold
from agent.storage.conversation_fold import ConversationFolder
from agent.storage.data_center import ConversationData
from agent.storage.storage_router import StorageDomain, StorageRouter

# 测试窗口参数：M=6，x=2（折叠周期 4 条）
MAX_SIZE = 6
RAW_MIN = 2


@pytest.fixture(autouse=True)
def _raw_min(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(conversation_fold, "raw_min_messages", lambda: RAW_MIN)
    monkeypatch.setattr(conversation_fold, "fold_hysteresis", lambda: 0)


@pytest.fixture
async def conv_data(sqlite):
    yield ConversationData(StorageRouter(sqlite=sqlite), max_size=MAX_SIZE)


def _anything() -> SimpleNamespace:
    return SimpleNamespace(scope_type="user", scope_id="1")


async def _append(conv_data: ConversationData, contents: list[str], start_ts: int = 0) -> list[int]:
    base = start_ts or time.time_ns()
    ts_list = []
    for i, content in enumerate(contents):
        ts = base + i
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content=content, ts_ns=ts,
        )
        ts_list.append(ts)
    return ts_list


class TestWindowFetch:
    async def test_below_window_returns_all(self, conv_data) -> None:
        """窗口未满：返回全部消息（无摘要行时行为与旧逻辑一致）。"""
        await _append(conv_data, ["m1", "m2", "m3"])
        records = await conv_data.get_conversation_record_by_everything(_anything())
        assert [r["content"] for r in records] == ["m1", "m2", "m3"]

    async def test_full_window_triggers_fold_schedule(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """水位线后消息数到达 M 时调度后台折叠。"""
        scheduled: list[dict] = []
        monkeypatch.setattr(
            conversation_fold.conversation_folder, "maybe_schedule_fold",
            lambda *a, **kw: scheduled.append({"args": a}) or True,
        )
        await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE)])
        records = await conv_data.get_conversation_record_by_everything(_anything())
        assert len(records) == MAX_SIZE
        assert len(scheduled) == 1

    async def test_below_window_no_fold(self, conv_data, monkeypatch: pytest.MonkeyPatch) -> None:
        scheduled: list = []
        monkeypatch.setattr(
            conversation_fold.conversation_folder, "maybe_schedule_fold",
            lambda *a, **kw: scheduled.append(1) or True,
        )
        await _append(conv_data, ["m1", "m2"])
        await conv_data.get_conversation_record_by_everything(_anything())
        assert not scheduled

    async def test_watermark_fetch_excludes_folded(self, conv_data) -> None:
        """存在摘要行时只取水位线之后的消息。"""
        ts_list = await _append(conv_data, [f"m{i}" for i in range(4)])
        sqlite = conv_data.router.sqlite
        await sqlite.upsert_conversation_summary(
            scope_type="user", scope_id="1",
            summary="旧摘要", watermarks={"user:1": ts_list[1]}, folded_count=2,
        )
        records = await conv_data.get_conversation_record_by_everything(_anything())
        assert [r["content"] for r in records] == ["m2", "m3"]

    async def test_grace_overflow_hard_degrades(self, conv_data) -> None:
        """折叠持续失败导致窗口超过 M+x：硬降级为最后 M 条滑动。"""
        ts_list = await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE + RAW_MIN + 2)])
        sqlite = conv_data.router.sqlite
        await sqlite.upsert_conversation_summary(
            scope_type="user", scope_id="1",
            summary="旧摘要", watermarks={"user:1": ts_list[0] - 1}, folded_count=0,
        )
        records = await conv_data.get_conversation_record_by_everything(_anything())
        assert len(records) == MAX_SIZE
        assert records[0]["content"] == f"m{RAW_MIN + 2}"
        assert records[-1]["content"] == f"m{MAX_SIZE + RAW_MIN + 1}"

    async def test_overflow_still_schedules_fold(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """积压超过宽限（折叠在途/重启期间消息继续到达）也必须调度折叠。

        回归：调度判定曾用截断后行数——积压超宽限时截到 M 条恒 < trigger，
        永不调度，水位线停滞、窗口逐条滑动（缓存前缀每条消息断裂的死态）。
        """
        scheduled: list = []
        monkeypatch.setattr(
            conversation_fold.conversation_folder, "maybe_schedule_fold",
            lambda *a, **kw: scheduled.append(1) or True,
        )
        sqlite = conv_data.router.sqlite
        from agent.storage.conversation_fold import fold_hysteresis
        grace = MAX_SIZE + fold_hysteresis() + RAW_MIN
        ts_list = await _append(conv_data, [f"m{i}" for i in range(grace + 5)])
        await sqlite.upsert_conversation_summary(
            scope_type="user", scope_id="1",
            summary="旧摘要", watermarks={"user:1": ts_list[0] - 1}, folded_count=0,
        )
        records = await conv_data.get_conversation_record_by_everything(_anything())
        assert len(records) == MAX_SIZE  # 返回仍硬降级为最后 M 条
        assert len(scheduled) == 1       # 但折叠必须被调度


class TestFoldExecution:
    async def _run_fold(self, conv_data: ConversationData, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_summarizer(prompt: str) -> str:
            return "折叠摘要内容"

        async def resolve():
            return fake_summarizer

        monkeypatch.setattr(ConversationFolder, "_resolve_summarizer", staticmethod(resolve))
        folder = ConversationFolder()
        await folder._fold(
            conv_data, "user", "1", [("user", "1")], {},
        )

    async def test_fold_advances_watermark_and_summary(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """折叠最旧 M-x 条：摘要落库、水位线推进到折叠最大 ts、窗口重置为 x 条。"""
        ts_list = await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE)])
        await self._run_fold(conv_data, monkeypatch)

        row = await conv_data.router.sqlite.get_conversation_summary(
            scope_type="user", scope_id="1",
        )
        assert row is not None
        assert row["summary"] == "折叠摘要内容"
        assert row["folded_count"] == MAX_SIZE - RAW_MIN
        # 水位线推进到第 M-x 条的 ts
        assert row["watermarks"]["user:1"] == ts_list[MAX_SIZE - RAW_MIN - 1]

        records = await conv_data.get_conversation_record_by_everything(_anything())
        assert [r["content"] for r in records] == [f"m{i}" for i in range(MAX_SIZE - RAW_MIN, MAX_SIZE)]

    async def test_fold_batch_capped(self, conv_data, monkeypatch: pytest.MonkeyPatch) -> None:
        """积压恢复时单批不超过上限：分多批消化，摘要提示词有界。"""
        from agent.storage import conversation_fold
        monkeypatch.setattr(conversation_fold, "fold_batch_max", lambda: 3)
        ts_list = await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE)])
        await self._run_fold(conv_data, monkeypatch)
        row = await conv_data.router.sqlite.get_conversation_summary(
            scope_type="user", scope_id="1",
        )
        assert row is not None
        # 批量上限 3：只折最旧 3 条（无上限时为 M-x=4），水位线只推进到第 3 条
        assert row["folded_count"] == 3
        assert row["watermarks"]["user:1"] == ts_list[2]

    async def test_window_append_only_after_fold(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """折叠后新消息纯追加：窗口前缀字节稳定（缓存命中的核心属性）。"""
        await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE)])
        await self._run_fold(conv_data, monkeypatch)

        before = await conv_data.get_conversation_record_by_everything(_anything())
        await _append(conv_data, ["new1"], start_ts=time.time_ns() + 1000)
        after = await conv_data.get_conversation_record_by_everything(_anything())

        # 新窗口 = 旧窗口 + 新消息（头部不变，只追加）
        assert after[: len(before)] == before
        assert after[-1]["content"] == "new1"

    async def test_fold_failure_drops_batch(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """折叠失败默认丢批：水位线推进 + dropped_count，窗口头部不滑动。"""
        async def failing_summarizer(prompt: str) -> str:
            raise RuntimeError("llm down")

        async def resolve():
            return failing_summarizer

        monkeypatch.setattr(ConversationFolder, "_resolve_summarizer", staticmethod(resolve))
        folder = ConversationFolder()
        ts_list = await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE)])
        await folder._fold(conv_data, "user", "1", [("user", "1")], {})

        row = await conv_data.router.sqlite.get_conversation_summary(
            scope_type="user", scope_id="1",
        )
        assert row is not None
        assert row["dropped_count"] == MAX_SIZE - RAW_MIN
        assert row["folded_count"] == 0
        assert row["summary"] == ""  # 摘要未生成
        # 水位线推进到丢弃批最大 ts：窗口头部不逐条滑动
        assert row["watermarks"]["user:1"] == ts_list[MAX_SIZE - RAW_MIN - 1]
        records = await conv_data.get_conversation_record_by_everything(_anything())
        assert [r["content"] for r in records] == [f"m{i}" for i in range(MAX_SIZE - RAW_MIN, MAX_SIZE)]

    async def test_fold_failure_no_drop_keeps_sliding(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """关闭丢批时：不落库、记退避，窗口维持滑动行为。"""
        monkeypatch.setattr(conversation_fold, "drop_on_failure", lambda: False)

        async def failing_summarizer(prompt: str) -> str:
            raise RuntimeError("llm down")

        async def resolve():
            return failing_summarizer

        monkeypatch.setattr(ConversationFolder, "_resolve_summarizer", staticmethod(resolve))
        folder = ConversationFolder()
        await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE)])
        await folder._fold(conv_data, "user", "1", [("user", "1")], {})

        row = await conv_data.router.sqlite.get_conversation_summary(
            scope_type="user", scope_id="1",
        )
        assert row is None
        # 失败退避记录（60s 内不再调度）
        assert folder._last_failure.get("user:1", 0) > 0

    async def test_partial_batch_not_folded(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """水位线后不足一批（M-x 条）时不折叠（防并发重复触发）。"""
        await _append(conv_data, ["m1", "m2"])
        await self._run_fold(conv_data, monkeypatch)
        row = await conv_data.router.sqlite.get_conversation_summary(
            scope_type="user", scope_id="1",
        )
        assert row is None


class TestSummaryAccessor:
    async def test_get_conversation_summary_none(self, conv_data) -> None:
        assert await conv_data.get_conversation_summary(_anything()) is None

    async def test_get_conversation_summary_row(self, conv_data) -> None:
        await conv_data.router.sqlite.upsert_conversation_summary(
            scope_type="user", scope_id="1",
            summary="摘要", watermarks={"user:1": 123}, folded_count=5,
        )
        row = await conv_data.get_conversation_summary(_anything())
        assert row is not None
        assert row["summary"] == "摘要"
        assert row["folded_count"] == 5


class TestLiveMaxSize:
    async def test_max_size_reads_config_live(self, conv_data) -> None:
        """未显式覆盖时 max_size 实时读配置（Web 调整即时生效，无需重启）。"""
        from core.config import ConfigManager

        data = ConversationData(StorageRouter(sqlite=conv_data.router.sqlite))
        ConfigManager.initialize()
        ConfigManager.set("max_conversation_size", 42)
        try:
            assert data.max_size == 42
        finally:
            ConfigManager.set("max_conversation_size", MAX_SIZE)

    async def test_explicit_override_wins(self, conv_data) -> None:
        """显式传入的 max_size 固定（测试/定制路径）。"""
        from core.config import ConfigManager

        ConfigManager.initialize()
        ConfigManager.set("max_conversation_size", 42)
        try:
            assert conv_data.max_size == MAX_SIZE  # 构造时显式传了 MAX_SIZE
        finally:
            ConfigManager.set("max_conversation_size", MAX_SIZE)


class TestFoldHysteresis:
    async def test_no_trigger_below_threshold(
        self, conv_data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """滞回 H=3：窗口达到 M+H 才触发折叠（减少折叠频率/缓存重写）。"""
        monkeypatch.setattr(conversation_fold, "fold_hysteresis", lambda: 3)
        scheduled: list = []
        monkeypatch.setattr(
            conversation_fold.conversation_folder, "maybe_schedule_fold",
            lambda *a, **kw: scheduled.append(1) or True,
        )
        # M+H-1 = 8 条：未达阈值不触发
        await _append(conv_data, [f"m{i}" for i in range(MAX_SIZE + 3 - 1)])
        await conv_data.get_conversation_record_by_everything(_anything())
        assert not scheduled
        # 达到 M+H = 9 条：触发
        await _append(conv_data, ["mx"], start_ts=time.time_ns() + 1000)
        await conv_data.get_conversation_record_by_everything(_anything())
        assert len(scheduled) == 1
