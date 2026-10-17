"""思维运行时装配：共享实例、上下文边界与后台服务依赖。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.runtime import mind_factory
from agent.skills.skill_store import SkillStore


@pytest.mark.parametrize("skills_enabled", [False, True])
async def test_factory_shares_state_and_finishes_wiring_before_publish(tmp_path, monkeypatch, skills_enabled):
    store = SkillStore(str(tmp_path / "skills"))
    registry, embedder = MagicMock(), MagicMock()
    monkeypatch.setattr(mind_factory, "get_skill_store", lambda: store)
    monkeypatch.setattr(mind_factory, "get_plugin_manager", lambda: SimpleNamespace(registry=registry))
    monkeypatch.setattr(mind_factory, "get_embedder", lambda kind: embedder)
    monkeypatch.setattr(mind_factory.Mind, "_skills_enabled", staticmethod(lambda: skills_enabled))

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
    for name in ("HeartbeatEngine", "ContextCompressor", "DelegationManager", "WorkflowEngine", "AutoCapturePipeline"):
        factories[name].assert_called_once_with(mind)
    registration.assert_called_once_with()
    assert mind.accept_feel.__self__ is mind
    assert mind.reflect.__self__ is mind
    assert not mind.is_reply and mind._reply_idle_event.is_set()
