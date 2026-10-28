"""上下文提供者实时注入（think_loop 尾部）单元测试。

布局约定：provider 快照每轮收集并置于工具链之后、exec_context 之前——
实时内容（时间/天气等）逐轮新鲜，其字节变化不打断工具链前缀缓存。
"""

from __future__ import annotations

import pytest
from helpers.think_loop_fakes import (
    FakeContext,
    FakeMind,
    end_reply_result,
    run_think_loop,
    tool_result,
)

from agent.mind.execution.state import ThinkMode
from core.context_provider import (
    ContextProviderRegistry,
    ProviderMeta,
    ProviderSnapshot,
)


@pytest.mark.parametrize("multimodal", [False, True])
async def test_explicit_skill_tags_load_bodies_and_follow_live_context_replacement(tmp_path, multimodal):
    from types import SimpleNamespace

    from agent.context.message_schema import normalize_for_send
    from agent.skills.skill_store import SkillStore
    from core.tags import tag_label

    store = SkillStore(str(tmp_path / "skills"))
    for name in ("message-rule", "context-old", "context-new", "history-only", "tool-only"):
        store.create(name=name, description=name, content=f"BODY_{name}")
    live = [tag_label("skill", "context-old")]

    async def provide(scope):
        return live[0]

    ContextProviderRegistry.register(ProviderMeta(name="external-context", provide_fn=provide))

    class ChangingMind(FakeMind):
        async def execute_tool(self, tc):
            result = await super().execute_tool(tc)
            live[0] = tag_label("skill", "context-new")
            return result

    mind = ChangingMind(rounds=[tool_result("", ["inspect"]), end_reply_result()], default_text=None,
                        context=FakeContext(exec_layer=True), tool_results={"inspect": tag_label("skill", "tool-only")})
    mind.recollection.skills = SimpleNamespace(store=store)
    text = tag_label("skill", "message-rule") + "处理订单"
    content = [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}}] if multimodal else text
    base = [{"role": "system", "content": "stable", "_layer": "stable"},
            {"role": "user", "content": tag_label("skill", "history-only"), "_layer": "conversation"},
            {"role": "user", "content": content, "_layer": "conversation"}]
    completion = {}
    await run_think_loop(mind, base_messages=base, completion=completion)
    first, second = mind.sent_messages
    assert all(messages[:len(base)] == base for messages in (first, second))
    assert "BODY_message-rule" in str(first) and "BODY_message-rule" in str(second)
    assert "BODY_context-old" in str(first) and "BODY_context-old" not in str(second)
    assert "BODY_context-new" in str(second) and "BODY_context-new" not in str(first)
    for name in ("message-rule", "context-old", "context-new"):
        assert store.get(name).use_count == 1
    for name in ("history-only", "tool-only"):
        assert store.get(name).use_count == 0
        assert f"BODY_{name}" not in str(mind.sent_messages)
    assert "BODY_" not in str(completion["messages"])
    skill_message = next(message for message in second if message.get("_source", {}).get("origin") == "skill_tag")
    assert skill_message["_layer"] == "provider" and skill_message["_source"]["skills"][0]["mode"] == "read"
    assert all("_source" not in message and "_layer" not in message for message in normalize_for_send(second))


async def test_tool_progress_refreshes_providers_without_automatic_recall(monkeypatch):
    from unittest.mock import AsyncMock

    recall = AsyncMock()
    judge = AsyncMock()
    monkeypatch.setattr("agent.memory.memory_retriever.MemoryRetriever.recall_split", recall)
    monkeypatch.setattr("agent.memory.probe.assess_optional_work", judge)
    monkeypatch.setattr("core.context_provider._COLLECT_FRESH_SECONDS", 0.0)
    counter = [0]
    _register_counter_provider(counter)
    base = [{"role": "system", "content": "stable", "_layer": "stable"},
            {"role": "user", "content": "继续检查部署问题", "_layer": "conversation"}]
    mind = FakeMind(rounds=[tool_result("", ["inspect"]), tool_result("", ["verify"]), end_reply_result()],
                    tool_results={"inspect": "旧节点配置发生变更", "verify": "配置检查完成"},
                    default_text=None, context=FakeContext(exec_layer=True))
    await run_think_loop(mind, base_messages=base)
    assert len(mind.sent_messages) == 3
    assert all(messages[:2] == base for messages in mind.sent_messages)
    assert all(any(f"第{index}次收集" in str(message) for message in messages)
               for index, messages in enumerate(mind.sent_messages, 1))
    recall.assert_not_awaited()
    judge.assert_not_awaited()


@pytest.mark.parametrize("mode", [ThinkMode.REPLY, ThinkMode.REFLECT])
async def test_only_main_reply_observes_new_cross_channel_messages(monkeypatch, mode):
    from unittest.mock import AsyncMock

    notice = {"role": "system", "content": "FOREIGN_USER_REQUEST",
              "_source": {"origin": "cross_channel_narrative"}}
    narrative = AsyncMock(return_value=[notice])
    monkeypatch.setattr("agent.mind.cross_channel.build_cross_channel_narrative", narrative)
    base = [{"role": "user", "content": "OWN_EXPLICIT_TASK_CONTEXT"}]
    mind = FakeMind(rounds=[tool_result("", ["inspect"]), end_reply_result()], default_text=None)
    await run_think_loop(mind, mode=mode, base_messages=base)
    assert len(mind.sent_messages) == 2
    assert all("OWN_EXPLICIT_TASK_CONTEXT" in str(sent) for sent in mind.sent_messages)
    if mode == ThinkMode.REPLY:
        assert narrative.await_count == 2
        assert all("FOREIGN_USER_REQUEST" in str(sent) for sent in mind.sent_messages)
    else:
        narrative.assert_not_awaited()
        assert all("FOREIGN_USER_REQUEST" not in str(sent) for sent in mind.sent_messages)


def _register_counter_provider(counter: list) -> None:
    async def _provide(scope: str) -> ProviderSnapshot:
        counter[0] += 1
        return ProviderSnapshot(content=f"[实时] 第{counter[0]}次收集")

    ContextProviderRegistry.register(
        ProviderMeta(name="fresh_demo", provide_fn=_provide),
    )


def _layer_of(msg: dict) -> str:
    return str(msg.get("_layer", ""))


class TestProviderTailInjection:
    async def test_live_messages_and_cross_session_notices_preserve_cached_prefix(self, sqlite):
        from copy import deepcopy
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from agent.context.message_schema import is_genuine_user_message, normalize_for_send
        from agent.llm.prompt_cache import decorate_messages
        from agent.messages import MessageUser
        from agent.mind.prefrontal_cortex import PrefrontalCortex
        from agent.storage.data_center import ConversationData
        from agent.storage.storage_router import StorageRouter

        data = ConversationData(StorageRouter(sqlite=sqlite))
        entity = SimpleNamespace(add_conversations_num=lambda: 0, personality={})
        pfc = PrefrontalCortex(SimpleNamespace(get_anything=AsyncMock(return_value=entity)))
        current = MessageUser(uid="1", adapter_key="qq", session_id="current", text_content="当前请求")
        other = MessageUser(uid="1", adapter_key="qq", session_id="other", text_content="其他会话旧请求")

        async def arrive(message):
            await data.add_conversation_record_by_everything(message)
            await pfc.add_task(message)

        await arrive(current)
        await arrive(other)

        class LiveMind(FakeMind):
            async def execute_tool(self, tc):
                result = await super().execute_tool(tc)
                if tc.name == "inspect":
                    pfc.consume_scope_task(other.entity_scope)
                    await arrive(MessageUser(uid="1", adapter_key="qq", session_id="current", text_content="当前会话补充"))
                    await arrive(MessageUser(uid="1", adapter_key="qq", session_id="other",
                                             text_content="[uid:别人][channel:telegram]其他会话新请求"))
                return result

        mind = LiveMind(rounds=[tool_result("", ["inspect"]), end_reply_result()], default_text=None,
                        context=FakeContext(exec_layer=True), pfc=pfc)
        mind.conversation_data = data
        prefix = [{"role": "system", "content": "固定人设", "_layer": "stable"},
                  *await data.get_conversation_record_by_everything(current)]
        frozen = deepcopy(prefix)
        await run_think_loop(mind, anything=current, base_messages=prefix)
        first, second = mind.sent_messages
        assert prefix == frozen
        assert first[:len(prefix)] == second[:len(prefix)] == frozen
        assert "其他会话旧请求" in str(first) and "其他会话旧请求" not in str(second)
        assert "当前会话补充" in str(second) and "其他会话新请求" in str(second)
        notification_positions = [i for i, message in enumerate(second)
                                  if message.get("_source", {}).get("origin") == "cross_channel_narrative"]
        assert max(i for i, message in enumerate(second) if message.get("role") == "tool") < min(notification_positions)
        assert max(notification_positions) < next(i for i, message in enumerate(second) if message.get("_layer") == "exec_context")
        for anthropic in (False, True):
            decorated = [decorate_messages(messages, anthropic=anthropic) for messages in (first, second)]
            assert all("cache_control" not in decorated[1][i] for i in notification_positions)
            wires = [normalize_for_send(messages) for messages in decorated]
            # 链尾缓存断点随工具链推进，前缀正文和角色保持逐字一致。
            assert [{k: v for k, v in m.items() if k != "cache_control"} for m in wires[0][:len(prefix)]] == [
                {k: v for k, v in m.items() if k != "cache_control"} for m in wires[1][:len(prefix)]]
            notice = next(message for message in wires[1] if "其他会话新请求" in str(message))
            assert notice["content"].startswith("[kind:notification][notification:session_message]")
            assert not is_genuine_user_message(notice)
        assert any(scope == other.entity_scope for scope, *_ in pfc.peek_all_tasks())
        assert not any(scope == current.entity_scope for scope, *_ in pfc.peek_all_tasks())
        history = await data.get_conversation_record_by_everything(current)
        assert "其他会话新请求" not in str(history)

    async def test_after_tool_chain_before_exec_context(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """两轮回复：provider 消息位于工具链之后、exec_context 之前，且逐轮新鲜。"""
        # 新鲜度阈值归零：每轮强制重收（验证逐轮新鲜语义）
        monkeypatch.setattr(
            "core.context_provider._COLLECT_FRESH_SECONDS", 0.0,
        )
        counter = [0]
        _register_counter_provider(counter)

        mind = FakeMind(
            rounds=[tool_result("", ["recall"]), end_reply_result()],
            default_text=None,
            context=FakeContext(exec_layer=True),
        )
        base = [{"role": "system", "content": "BASE", "_layer": "stable"}]
        await run_think_loop(
            mind, mode=ThinkMode.REPLY, base_messages=base, tools=[{"name": "recall"}],
        )

        assert len(mind.sent_messages) == 2
        round2 = mind.sent_messages[1]

        provider_idx = next(
            i for i, m in enumerate(round2) if _layer_of(m) == "provider"
        )
        exec_idx = next(
            i for i, m in enumerate(round2) if _layer_of(m) == "exec_context"
        )
        # 工具链消息无 _layer 标签（recall 的 assistant/tool 回合）
        chain_idx = max(
            i for i, m in enumerate(round2)
            if not m.get("_layer") and m.get("role") != "system"
            or m.get("role") == "tool"
        )
        assert chain_idx < provider_idx < exec_idx

        # 逐轮新鲜：两轮收集内容不同（计数递增）
        r1_provider = next(
            m for m in mind.sent_messages[0] if _layer_of(m) == "provider"
        )
        r2_provider = round2[provider_idx]
        assert r1_provider["content"] == "[实时] 第1次收集"
        assert r2_provider["content"] == "[实时] 第2次收集"

    async def test_no_providers_no_injection(self) -> None:
        """无注册 provider 时消息结构不受影响（零开销零残留）。"""
        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            context=FakeContext(exec_layer=True),
        )
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        round1 = mind.sent_messages[0]
        assert all(_layer_of(m) != "provider" for m in round1)


class TestProviderMediaDispatch:
    """clip 媒体按 kind 分派：image 视觉直注 / 降级标签；audio/video 一律标签引用。"""

    def _register_media_provider(self, media: list, text: str = "[环境] 桌面状态") -> None:
        async def _provide(scope: str) -> ProviderSnapshot:
            return ProviderSnapshot(content=text, media=media)

        ContextProviderRegistry.register(
            ProviderMeta(name="media_demo", provide_fn=_provide),
        )

    def _vision_mind(self) -> FakeMind:
        from types import SimpleNamespace

        from agent.llm.config import LLMClientConfig

        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            context=FakeContext(exec_layer=True),
        )
        mind.models.llm = SimpleNamespace(
            config=LLMClientConfig(name="vision", supports_vision=True),
        )
        return mind

    async def test_image_direct_injected_for_vision_model(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """视觉模型：图片组 user 角色多模态消息（image block + 说明文本）。"""
        import base64

        from agent.llm.types import ImageContent

        async def _fake_report(images, *a, **kw):
            return [
                ImageContent(
                    data=base64.b64encode(b"x").decode(), mime_type="image/jpeg",
                ),
            ], [], []

        monkeypatch.setattr("agent.llm.image_utils.ensure_base64_report", _fake_report)
        from core.context_provider import ContextMedia
        self._register_media_provider([ContextMedia.image("/tmp/shot.png")])

        mind = self._vision_mind()
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider" and m["role"] == "system")
        assert sys_msg["content"] == "[环境] 桌面状态"
        media_msg = next(
            m for m in msgs if _layer_of(m) == "provider" and m["role"] == "user"
        )
        assert any(b["type"] == "image_url" for b in media_msg["content"])
        # 图片直注后 clip 文本不再附带媒体标签（避免双重表达）
        assert "[media_type:" not in sys_msg["content"]

    async def test_image_degrades_to_tag_without_vision(self) -> None:
        """非视觉模型：图片降级为媒体标签并入 clip 文本（AI 走媒体工具）。"""
        from core.context_provider import ContextMedia
        self._register_media_provider([ContextMedia.image("/tmp/shot.png")])

        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            context=FakeContext(exec_layer=True),
        )  # 无 llm 配置 = 非视觉
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider")
        assert "[media_type:image][media_path:/tmp/shot.png]" in sys_msg["content"]
        assert all(m["role"] != "user" for m in msgs if _layer_of(m) == "provider")

    async def test_audio_video_always_tag_reference(self) -> None:
        """audio/video 即使视觉模型也走标签引用（对话协议层不接受这两类 block）。"""
        from core.context_provider import ContextMedia
        self._register_media_provider([
            ContextMedia.audio("/tmp/env.wav"),
            ContextMedia.video("/tmp/clip.mp4"),
        ])

        mind = self._vision_mind()
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider" and m["role"] == "system")
        assert "[media_type:audio][media_path:/tmp/env.wav]" in sys_msg["content"]
        assert "[media_type:video][media_path:/tmp/clip.mp4]" in sys_msg["content"]
        assert all(m["role"] != "user" for m in msgs if _layer_of(m) == "provider")

    async def test_media_only_clip_creates_reference_message(self) -> None:
        """纯媒体 clip（无文本）也产出标签消息，不静默丢失。"""
        from core.context_provider import ContextMedia
        self._register_media_provider([ContextMedia.audio("/tmp/env.wav")], text="")

        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            context=FakeContext(exec_layer=True),
        )
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider")
        assert sys_msg["content"].startswith("[环境媒体]")
        assert "[media_type:audio]" in sys_msg["content"]
