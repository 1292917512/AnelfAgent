"""思维运行时装配：共享实例、上下文边界与后台服务依赖。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.config import BotConfig, BotConfigProvider
from agent.llm import ChatResult
from agent.runtime import mind_factory
from agent.skills.skill_store import SkillStore
from core.config import ConfigManager


@pytest.mark.parametrize("skills_enabled", [False, True])
async def test_factory_shares_state_and_finishes_wiring_before_publish(tmp_path, monkeypatch, skills_enabled):
    store = SkillStore(str(tmp_path / "skills"))
    registry, embedder = MagicMock(), MagicMock()
    monkeypatch.setattr(mind_factory, "get_skill_store", lambda: store)
    monkeypatch.setattr(mind_factory, "get_plugin_manager", lambda: SimpleNamespace(registry=registry))
    monkeypatch.setattr(mind_factory, "get_embedder", lambda kind: embedder)
    ConfigManager.set("skills_enabled", skills_enabled)
    ConfigManager.set("mind_config_path", str(tmp_path / "mind.json"))
    provider = object.__new__(BotConfigProvider)
    provider._config = BotConfig()
    provider._cm_available = True
    monkeypatch.setattr(mind_factory, "get_mind_config", lambda: provider.mind)

    # Persistent collaborators are isolated; Mind, PFC and ContextAssembly remain real.
    factories = {}
    for module, name in [
        ("agent.runtime.mind_factory", "HeartbeatEngine"),
        ("agent.runtime.mind_factory", "ContextCompressor"),
        ("agent.runtime.mind_factory", "MemoryRetriever"),
        ("agent.skills", "SkillMatcher"),
        ("agent.skills", "SkillCurator"),
        ("agent.skills", "SkillReviewer"),
        ("agent.delegation.delegation_manager", "DelegationManager"),
        ("agent.workflow.engine", "WorkflowEngine"),
        ("agent.memory.auto_capture", "AutoCapturePipeline"),
    ]:
        factories[name] = MagicMock()
        monkeypatch.setattr(f"{module}.{name}", factories[name])
    registration = MagicMock()
    monkeypatch.setattr(mind_factory, "register_core_tools", registration)
    monkeypatch.setattr(mind_factory, "activate_group", MagicMock(return_value=1))

    conversation, manager, memory = MagicMock(), MagicMock(), MagicMock()
    manager.chat_with_fallback = AsyncMock(return_value=ChatResult(content=""))
    mind = mind_factory.create_mind(
        char=MagicMock(), llm=MagicMock(), llm_manager=manager,
        channel_manager=MagicMock(), everything_data=MagicMock(),
        conversation_data=conversation, storage_router=None, memory_store=memory,
    )
    assert mind.context._work_memory is mind.pfc.work_memory
    assert mind.context._tool_assembly is mind.pfc.tool_assembly
    assert mind.pfc.work_memory.tool_assembly is mind.pfc.tool_assembly
    assert mind.context._skill_store is mind.skill_store is store
    assert mind.context._plugin_registry is registry
    assert mind.context.preparation.assembly is mind.context
    assert not hasattr(mind.pfc, "build_llm_context")
    factories["MemoryRetriever"].assert_called_once_with(memory, embedder)
    mind.retriever.set_rerank_client.assert_called_once_with(manager.get_media_client.return_value)
    factories["SkillMatcher"].assert_called_once_with(store, embedder)
    factories["SkillCurator"].assert_called_once_with(store, mind.skill_matcher.index)
    factories["SkillReviewer"].assert_called_once_with(mind, store, index=mind.skill_matcher.index)
    assert mind.skill_reviewer.start.call_count == int(skills_enabled)
    for name in ("HeartbeatEngine", "DelegationManager", "WorkflowEngine", "AutoCapturePipeline"):
        factories[name].assert_called_once_with(mind)
    factories["ContextCompressor"].assert_called_once_with(mind.model_invoker)
    registration.assert_called_once_with()
    assert mind.accept_feel.__self__ is mind
    assert mind.reflect.__self__ is mind
    assert not mind.is_reply and mind._reply_idle_event.is_set()
    assert mind.config is provider.mind
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

    def fail_write(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("core.file_utils.atomic_write_text", fail_write)
    with pytest.raises(OSError, match="disk unavailable"):
        provider.save_mind_config(llm_timeout=90)
    assert mind.config.llm_timeout == 75
