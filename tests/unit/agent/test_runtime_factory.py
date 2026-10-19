"""运行时装配：共享实例、上下文边界与后台服务依赖。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.config import BotConfig, BotConfigProvider
from agent.llm import ChatResult
from agent.llm.llm_client import LLMClient, LLMClientConfig
from agent.runtime import factory
from agent.runtime.runtime import AgentRuntime
from agent.skills.skill_store import SkillStore
from core.config import ConfigManager
from services.memory import MemoryService


@pytest.mark.parametrize("skills_enabled", [False, True])
async def test_factory_shares_state_and_finishes_wiring_before_publish(tmp_path, monkeypatch, skills_enabled):
    store = SkillStore(str(tmp_path / "skills"))
    registry, embedder = MagicMock(), MagicMock()
    monkeypatch.setattr("agent.skills.get_skill_store", lambda: store)
    monkeypatch.setattr("core.plugins.get_plugin_manager", lambda: SimpleNamespace(registry=registry))
    monkeypatch.setattr("agent.memory.embedding.get_embedder", lambda kind: embedder)
    ConfigManager.set("skills_enabled", skills_enabled)
    ConfigManager.set("mind_config_path", str(tmp_path / "mind.json"))
    provider = object.__new__(BotConfigProvider)
    provider._config = BotConfig()
    provider._cm_available = True
    monkeypatch.setattr("agent.config.get_mind_config", lambda: provider.mind)

    # Persistent collaborators are isolated; state and context assembly remain real.
    factories = {}
    for module, name in [
        ("agent.heartbeat.engine", "HeartbeatEngine"),
        ("agent.context.compressor", "ContextCompressor"),
        ("agent.memory.memory_retriever", "MemoryRetriever"),
        ("agent.skills", "SkillMatcher"),
        ("agent.skills", "SkillCurator"),
        ("agent.skills", "SkillReviewer"),
        ("agent.delegation.delegation_manager", "DelegationManager"),
    ]:
        factories[name] = MagicMock()
        monkeypatch.setattr(f"{module}.{name}", factories[name])
    registration = MagicMock()
    monkeypatch.setattr("agent.mind.tools.core.register_core_tools", registration)
    monkeypatch.setattr("core.tool_registry.activate_group", MagicMock(return_value=1))
    woke = asyncio.Event()
    wake = MagicMock(side_effect=woke.set)
    monkeypatch.setattr("agent.mind.driver.MindDriver.request_cycle", wake)

    conversation, manager, memory, everything = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    conversation.router.append = AsyncMock()
    manager.chat_with_fallback = AsyncMock(return_value=ChatResult(content=""))
    runtime = AgentRuntime(
        channel_manager=MagicMock(), pipeline=MagicMock(), char=MagicMock(),
        config=provider.mind, llm=MagicMock(), llm_manager=manager,
        data_center=SimpleNamespace(conversation_data=conversation, everything_data=everything),
        memory_store=memory, embedder=embedder,
    )
    mind = factory.create_mind(runtime)
    runtime.mind, runtime.pfc = mind, mind.pfc
    runtime.assistant, runtime.workflow_engine = MagicMock(), MagicMock()
    monkeypatch.setattr("services.memory.require_runtime", lambda: runtime)
    assert mind.context._pfc is mind.pfc is runtime.pfc
    memory_service = MemoryService()
    notice = {"role": "system", "content": "scope-specific notice"}
    mind.pfc.add_temporary(notice, scope="user_webui:u1")
    assert memory_service.list_stm() == [notice]
    assert memory_service.delete_stm(0)
    assert mind.pfc.temporary == []
    runtime.pfc.add_temporary(notice)
    assert memory_service.clear_stm() == 1
    assert mind.context._pfc.temporary == []
    assert mind.context._tool_assembly is mind.tool_assembly
    assert mind.pfc.tool_assembly is mind.tool_assembly
    factories["HeartbeatEngine"].assert_called_once_with(mind, everything, memory=runtime, skills=runtime.skills)
    assert mind.context._skill_store is runtime.skills.store is store
    assert mind.recollection.skills is runtime.skills
    assert mind.memory_store is mind.recollection.memory_store is runtime.memory_store
    assert mind.embedder is mind.recollection.embedder is runtime.embedder
    assert mind.retriever is mind.recollection.retriever is runtime.retriever
    assert not hasattr(mind, "skills") and not hasattr(mind, "delegation_manager")
    monkeypatch.setattr("services._runtime.get_runtime", lambda: runtime)
    from services.skill import _runtime_index
    assert _runtime_index() is runtime.skills.matcher.index
    assert mind.context._plugin_registry is registry
    assert mind.context.preparation.assembly is mind.context
    assert not hasattr(mind.pfc, "build_llm_context")
    factories["MemoryRetriever"].assert_called_once_with(memory, embedder)
    mind.retriever.set_rerank_client.assert_called_once_with(manager.get_media_client.return_value)
    factories["SkillMatcher"].assert_called_once_with(store, embedder)
    factories["SkillCurator"].assert_called_once_with(store, runtime.skills.matcher.index)
    factories["SkillReviewer"].assert_called_once_with(mind, store, index=runtime.skills.matcher.index)
    assert factories["SkillReviewer"].return_value.start.call_count == int(skills_enabled)
    factories["DelegationManager"].assert_called_once_with(mind)
    factories["ContextCompressor"].assert_called_once_with(mind.model_invoker)
    registration.assert_called_once_with()
    assert mind.accept_feel.__self__ is mind
    assert mind.reflect.__self__ is mind
    assert not mind.is_reply and mind._reply_idle_event.is_set()
    assert mind.config is provider.mind
    assert mind.models is runtime
    assert not hasattr(mind, "llm")
    provider.save_mind_config(llm_timeout=45, llm_max_retries=2)
    assert mind.config.llm_timeout == 45
    assert mind.config.llm_max_retries == 2
    await mind.model_invoker.chat_with_retry([], None)
    call = manager.chat_with_fallback.call_args
    assert call.kwargs["timeout"] == 45
    assert call.kwargs["max_retries"] == 2
    (tmp_path / "mind.json").write_text(json.dumps({"llm_timeout": 75}), encoding="utf-8")
    provider.reload()
    assert mind.config is provider.mind
    assert mind.config.llm_timeout == 75
    await mind.model_invoker.chat_with_retry([], None)
    assert manager.chat_with_fallback.call_args.kwargs["timeout"] == 75

    replacement = object.__new__(LLMClient)
    replacement.config = LLMClientConfig(name="replacement", context_window=64000)
    monkeypatch.setattr(LLMClient, "get_model_info", staticmethod(lambda _: {}))
    runtime.llm = replacement
    mind.model_invoker.session_params["temperature"] = 0.3
    await mind.model_invoker.chat_with_retry([], None)
    assert manager.chat_with_fallback.call_args.kwargs["client"] is replacement
    assert manager.chat_with_fallback.call_args.kwargs["options"]["temperature"] == 0.3
    assert mind.models.llm is replacement
    assert mind.model_invoker.get_context_length() == 64000

    def fail_write(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("core.file_utils.atomic_write_text", fail_write)
    with pytest.raises(OSError, match="disk unavailable"):
        provider.save_mind_config(llm_timeout=90)
    assert mind.config.llm_timeout == 75

    scope = "user_webui:u1"
    try:
        assert await asyncio.to_thread(mind.push_hub.push, scope, "test", "推送通知", "webui")
        await asyncio.wait_for(woke.wait(), timeout=1)
        assert mind.push_hub.current_seq(scope) == 1
        assert conversation.router.append.await_count == 1
        assert "推送通知" in conversation.router.append.call_args.kwargs["content"]
        assert memory_service.get_pending_status() == [{
            "role": "pending", "content": "[待处理消息: user_webui:u1] 实体推送 test: 推送通知 [webui]",
        }]

        woke.clear()
        task = mind.background_tasks.register(scope, "shell", "后台操作")
        await asyncio.to_thread(mind.background_tasks.complete, task, True, "已完成")
        await asyncio.wait_for(woke.wait(), timeout=1)
        assert conversation.router.append.await_count == 2
        assert "[后台任务完成]" in conversation.router.append.call_args.kwargs["content"]

        woke.clear()
        running = mind.background_tasks.register(scope, "shell", "运行中操作")
        await asyncio.to_thread(mind.background_tasks.alert_timeout, running, "检查进度")
        await asyncio.wait_for(woke.wait(), timeout=1)
        assert conversation.router.append.await_count == 3
        assert "[后台任务进度报告]" in conversation.router.append.call_args.kwargs["content"]
        assert len(mind.background_tasks.running(scope)) == 1
    finally:
        await mind.background_tasks.close()
        await mind.push_hub.close()
