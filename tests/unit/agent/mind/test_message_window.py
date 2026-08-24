"""消息窗口时序（到达即写历史 + 循环内合并）单元测试。

复现问题场景：用户连续发送两条消息，第二条在 AI 回复期间到达。
修复前第二条消息等 LLM 分析结束后才入库（时序错乱，排在 AI 回复之后）；
修复后到达即按到达时间戳写入对话历史，并被 think_loop 合并机制并入当前上下文。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List

import pytest
from helpers.think_loop_fakes import FakeMind, FakePfc, run_think_loop

from agent.llm.types import ImageContent, ToolCall
from agent.messages import MessageUser
from agent.storage.data_center import ConversationData
from agent.storage.storage_router import StorageDomain, StorageRouter
from core.tags import format_time, get_time_tag

# ------------------------------------------------------------------
# 到达时间戳与时间标签
# ------------------------------------------------------------------

class TestArrivalTimestamp:
    def test_time_tag_uses_created_ts(self) -> None:
        """时间标签取消息到达时间，而非字符串化时刻。"""
        ts = time.time_ns() - 60 * 1_000_000_000  # 1 分钟前到达
        msg = MessageUser(uid=1, created_ts_ns=ts)
        msg.set_text_content("你好")
        assert get_time_tag(ts) in str(msg)

    def test_time_tag_stable_across_calls(self) -> None:
        """多次字符串化时间标签不变（修复前每次取当前时间，处理完时间被改写）。"""
        msg = MessageUser(uid=1)
        msg.set_text_content("你好")
        first = str(msg)
        time.sleep(0.02)
        assert str(msg) == first

    def test_format_time_matches_time_tag(self) -> None:
        ts = time.time_ns()
        assert format_time(ts) == get_time_tag(ts)[len("[time:"):-1]


# ------------------------------------------------------------------
# 对话历史入库时序
# ------------------------------------------------------------------

@pytest.fixture
async def conv_data(sqlite):
    yield ConversationData(StorageRouter(sqlite=sqlite))


class TestConversationOrdering:
    async def test_write_uses_arrival_ts(self, conv_data) -> None:
        """入库 ts_ns 为消息到达时间，保证历史严格按到达时序排列。"""
        arrival = time.time_ns() - 30 * 1_000_000_000
        msg = MessageUser(uid=1, created_ts_ns=arrival)
        msg.set_text_content("第一条")
        await conv_data.add_conversation_record_by_everything(msg)

        rows = await conv_data.router.sqlite.fetch_conversation(
            scope_type="user", scope_id="1", limit=10,
        )
        assert len(rows) == 1
        assert rows[0]["ts_ns"] == arrival

    async def test_late_processed_message_keeps_arrival_order(self, conv_data) -> None:
        """AI 回复先入库、用户消息（到达更早）后入库时，历史仍按到达时序排列。"""
        arrival = time.time_ns() - 5 * 1_000_000_000
        msg = MessageUser(uid=1, created_ts_ns=arrival)
        msg.set_text_content("处理期间到达的消息")
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="assistant", content="AI 回复",
        )
        await conv_data.add_conversation_record_by_everything(msg)

        rows = await conv_data.router.sqlite.fetch_conversation(
            scope_type="user", scope_id="1", limit=10,
        )
        assert [r["role"] for r in rows] == ["user", "assistant"]

    async def test_fetch_records_watermark_and_strips_ts(self, conv_data) -> None:
        """快照记录最大 ts_ns 水位，且返回记录不携带 ts_ns（防泄漏进 LLM 消息）。"""
        t1 = time.time_ns()
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content="m1", ts_ns=t1,
        )
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content="m2", ts_ns=t1 + 1,
        )
        records = await conv_data.get_conversation_record_by_everything(MessageUser(uid=1))

        assert records == [
            {"role": "user", "content": "m1"},
            {"role": "user", "content": "m2"},
        ]
        assert conv_data.get_fetch_watermark("user", "1") == t1 + 1
        assert conv_data.get_fetch_watermark("user", "999") is None


# ------------------------------------------------------------------
# feel() 到达即感知
# ------------------------------------------------------------------

class _RecordingMind:
    """记录 accept_feel 调用顺序的最小 Mind 替身。"""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.is_reflecting = False
        self.is_reply = False
        self.pfc = SimpleNamespace(
            pending_analysis=SimpleNamespace(is_empty=lambda: True),
            peek_general_tasks=lambda: [],
        )

    async def accept_feel(self, anything) -> None:
        self.calls.append("accept_feel")

    async def execute_mind(self, *, is_heartbeat: bool = False) -> None:
        self.calls.append("execute_mind")


class TestFeelImmediateAccept:
    async def test_feel_accepts_before_queue(self) -> None:
        """feel() 先感知（写历史 + 入 PFC 队列）再入队，不等 Mind 空闲。"""
        from agent.runtime.assistant import AgentAssistant

        mind = _RecordingMind()
        assistant = AgentAssistant(mind, heartbeat_enabled=False)  # type: ignore[arg-type]
        msg = MessageUser(uid=1)
        msg.set_text_content("你好")
        await assistant.feel(msg)

        assert mind.calls == ["accept_feel"]
        assert assistant._queue.qsize() == 1
        await assistant.stop()

    async def test_feel_drops_message_on_accept_failure(self) -> None:
        """感知失败的消息不入队（与旧批量路径的失败语义一致）。"""
        from agent.runtime.assistant import AgentAssistant

        class _FailingMind(_RecordingMind):
            async def accept_feel(self, anything) -> None:
                raise RuntimeError("db down")

        assistant = AgentAssistant(_FailingMind(), heartbeat_enabled=False)  # type: ignore[arg-type]
        msg = MessageUser(uid=1)
        msg.set_text_content("你好")
        await assistant.feel(msg)
        assert assistant._queue.qsize() == 0
        await assistant.stop()


# ------------------------------------------------------------------
# think_loop 循环内合并
# ------------------------------------------------------------------

class _MergePfc(FakePfc):
    """记录媒体收集/工具重建调用次数的 PFC 替身。"""

    def __init__(self) -> None:
        super().__init__()
        self.consumed: List[int] = []
        self.images_drained = 0
        self.media_drained = 0
        self.pending_images: list = []
        self.pending_media: list = []
        self.media_activated: List[tuple] = []
        self.tools_rebuilt = 0

    def consume_scope_task(self, scope) -> bool:
        self.consumed.append(scope)
        return True

    def collect_images(self, scope: str = "") -> list:
        self.images_drained += 1
        images, self.pending_images = self.pending_images, []
        return images

    def collect_media(self, scope: str = "") -> list:
        self.media_drained += 1
        media, self.pending_media = self.pending_media, []
        return media

    def activate_media_tools(self, images: list, media_segments: list) -> None:
        self.media_activated.append((images, media_segments))

    async def get_active_tool_schemas(self, adapter_key: str = "", scope: str = "") -> list:
        self.tools_rebuilt += 1
        return []


class _MergeMind(FakeMind):
    """LLM 首轮即 end_reply 的 Mind 替身，conversation_data 为真实临时库。"""

    def __init__(self, conv_data: ConversationData) -> None:
        super().__init__(
            pfc=_MergePfc(),
            config_overrides={"force_tool_use": True},
            tool_results={"end_reply": '{"ok": true, "action": "end_reply"}'},
        )
        self.conversation_data = conv_data

    def _resolve_adapter_key(self) -> str:
        return "test"

    @staticmethod
    def _resolve_scope(anything) -> tuple[str, str]:
        return anything.scope_type, anything.scope_id

    @staticmethod
    def _resolve_entity_scope(anything) -> str:
        return f"user_{anything.uid}" if anything else ""

    async def _invoke_llm_unified(self, messages, tools, anything=None, *, tool_choice=None, options=None,
        stream=False, on_delta=None, purpose="reply"):
        self.llm_calls += 1
        self.sent_messages.append(list(messages))
        return SimpleNamespace(
            content="",
            tool_calls=[ToolCall(id="c1", name="end_reply", arguments="{}")],
            reasoning_content="",
            usage=None,
            raw=None,
            model="fake",
        )


class TestThinkLoopMerge:
    async def test_merges_message_arrived_after_snapshot(self, conv_data) -> None:
        """快照后到达（已实时入库）的新消息并入当前上下文，并消费待处理条目。"""
        t1 = time.time_ns()
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content="第一条", ts_ns=t1,
        )
        # 快照（水位=t1）
        base_records = await conv_data.get_conversation_record_by_everything(MessageUser(uid=1))
        assert conv_data.get_fetch_watermark("user", "1") == t1

        # 循环期间到达的第二条消息：到达即入库（ts > 水位）
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content="第二条", ts_ns=t1 + 1,
        )

        mind = _MergeMind(conv_data)
        await run_think_loop(
            mind, anything=MessageUser(uid=1),
            safety_limit=5, base_messages=list(base_records),
        )

        # LLM 收到的上下文中包含第二条消息
        flat = [m.get("content") for m in mind.sent_messages[0]]
        assert "第二条" in flat
        # 待处理条目已消费（不会另起周期重复回复）
        assert mind.pfc.consumed == ["user_1"]
        # 待处理媒体已清空（媒体标签随内容并入，不残留到后续周期）
        assert mind.pfc.images_drained == 1
        assert mind.pfc.media_drained == 1

    async def test_snapshot_covered_message_not_remerged(self, conv_data) -> None:
        """快照已覆盖的消息（ts <= 水位）不重复并入。"""
        t1 = time.time_ns()
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content="第一条", ts_ns=t1,
        )
        base_records = await conv_data.get_conversation_record_by_everything(MessageUser(uid=1))

        mind = _MergeMind(conv_data)
        await run_think_loop(
            mind, anything=MessageUser(uid=1),
            safety_limit=5, base_messages=list(base_records),
        )

        flat = [m.get("content") for m in mind.sent_messages[0]]
        assert flat.count("第一条") == 1
        assert mind.pfc.consumed == []

    async def test_merge_with_media_activates_tools(self, conv_data) -> None:
        """并入携带媒体的新消息时：激活媒体工具并重建工具集（供后续轮次识别图片）。"""
        t1 = time.time_ns()
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user", content="第一条", ts_ns=t1,
        )
        base_records = await conv_data.get_conversation_record_by_everything(MessageUser(uid=1))
        await conv_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type="user", scope_id="1", role="user",
            content="看看这个\n[media_type:image][media_path:/tmp/x.jpg]", ts_ns=t1 + 1,
        )

        mind = _MergeMind(conv_data)
        mind.pfc.pending_images = [ImageContent(data="/tmp/x.jpg")]
        await run_think_loop(
            mind, anything=MessageUser(uid=1),
            safety_limit=5, base_messages=list(base_records),
        )

        # 媒体工具已激活且工具集已重建
        assert len(mind.pfc.media_activated) == 1
        assert mind.pfc.media_activated[0][0] and not mind.pfc.media_activated[0][1]
        assert mind.pfc.tools_rebuilt == 1


# ------------------------------------------------------------------
# 媒体工具激活（PFC.activate_media_tools）
# ------------------------------------------------------------------

class TestActivateMediaTools:
    def test_activates_by_image_and_segment_type(self) -> None:
        """按图片与媒体段类型激活对应标签工具（recognize_image / voice_to_text）。"""
        from agent.mind.prefrontal_cortex import PrefrontalCortex
        from core.entity import EntityRegistry

        EntityRegistry.register_tool(
            name="test_recognize_image_x", func=lambda: "s",
            group="media", tags=["media:image"],
        )
        EntityRegistry.register_tool(
            name="test_voice_to_text_x", func=lambda: "s",
            group="media", tags=["media:voice"],
        )
        try:
            pfc = PrefrontalCortex(
                everything_data=SimpleNamespace(),
                channel_manager=None,
                conversation_data=SimpleNamespace(),
            )
            seg = SimpleNamespace(type=SimpleNamespace(value="voice"))
            pfc.activate_media_tools([SimpleNamespace(data="/tmp/x.jpg")], [seg])

            assert "test_recognize_image_x" in pfc.tool_assembly.tag_activated_tools
            assert "test_voice_to_text_x" in pfc.tool_assembly.tag_activated_tools
        finally:
            EntityRegistry.unregister("test_recognize_image_x")
            EntityRegistry.unregister("test_voice_to_text_x")

    def test_no_media_no_activation(self) -> None:
        from agent.mind.prefrontal_cortex import PrefrontalCortex

        pfc = PrefrontalCortex(
            everything_data=SimpleNamespace(),
            channel_manager=None,
            conversation_data=SimpleNamespace(),
        )
        pfc.activate_media_tools([], [])
        assert not pfc.tool_assembly.tag_activated_tools


class TestInterjectionGuardrailReset:
    """用户插话重置工具守卫链（对齐 dsh repeat-tool-reminder）。"""

    @staticmethod
    def _genuine_user_msg(text: str) -> dict:
        """真实渠道到达的用户消息（带到达元数据标签）。"""
        return {"role": "user", "content": f"[time:2026年08月14日][uid:10001] {text}"}

    def test_genuine_user_message_resets_guardrail(self) -> None:
        from agent.mind.guardrails import GuardrailConfig, GuardrailController
        from agent.mind.tools.round_helpers import _maybe_reset_guardrail_for_interjection
        ctl = GuardrailController(GuardrailConfig(enabled=True, exact_failure_warn_after=2))
        # 先累积失败计数
        ctl.after_call("read_file", '{"p": "/a"}', '{"error": "x"}')
        assert _maybe_reset_guardrail_for_interjection(ctl, [self._genuine_user_msg("等一下")])
        # 重置后计数归零：再失败一次不触发 warn（阈值 2）
        d = ctl.after_call("read_file", '{"p": "/a"}', '{"error": "x"}')
        assert d.action == "allow"

    def test_machine_user_message_no_reset(self) -> None:
        from agent.mind.guardrails import GuardrailConfig, GuardrailController
        from agent.mind.tools.round_helpers import _maybe_reset_guardrail_for_interjection
        ctl = GuardrailController(GuardrailConfig(enabled=True, exact_failure_warn_after=2))
        ctl.after_call("read_file", '{"p": "/a"}', '{"error": "x"}')
        # 机器生成的 user 消息（无到达标签）不触发重置
        assert not _maybe_reset_guardrail_for_interjection(
            ctl, [{"role": "user", "content": "[系统任务] 继续"}])
        # 计数仍在：再失败一次即达 warn 阈
        d = ctl.after_call("read_file", '{"p": "/a"}', '{"error": "x"}')
        assert d.action == "warn"

    def test_empty_msgs_no_reset(self) -> None:
        from agent.mind.guardrails import GuardrailConfig, GuardrailController
        from agent.mind.tools.round_helpers import _maybe_reset_guardrail_for_interjection
        ctl = GuardrailController(GuardrailConfig(enabled=True))
        assert not _maybe_reset_guardrail_for_interjection(ctl, [])
