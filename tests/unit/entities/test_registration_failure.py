"""部分导入失败不留下工具、配置、提供者或生命周期声明。"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from agent.audio.providers import get_audio_registry
from agent.audio.source_fetch import list_source_fetchers
from agent.hooks_llm.spec import HookRegistry
from agent.retrieval import providers
from agent.tts.providers import get_tts_registry
from agent.vision.capabilities import get_visual_router
from agent.vision.framework import get_source
from core.config import ConfigManager, ConfigRegistry
from core.context_provider import ContextProviderRegistry
from core.entity import EntityRegistry
from core.event_bus import event_bus
from core.latebind import _BINDINGS
from core.lifecycle import Lifecycle
from entities import hotplug

MODULE = "entities._registration_probe"
NAME = "_registration_probe"

DECLARATIONS = '''
from entities._sdk import (
    tool, entity, register_tts_provider, register_audio_provider,
    register_visual_provider, register_retrieval_provider, register_vision_source,
    register_audio_source_fetcher,
)
from core.config import ConfigManager, register_configs
from core.context_provider import ContextProviderRegistry, ProviderMeta
from core.event_bus import event_bus
from core.lifecycle import Lifecycle

entity("_registration_probe", "probe")
register_configs({"entity/_registration_probe": {"_registration_probe_key": {"default": "value", "description": "probe"}}})

@tool(name="_registration_probe", group="_registration_probe")
async def run(value: list[str]) -> str:
    return "ok"

async def receive(payload):
    pass
event_bus.on("_registration_probe", receive)
ConfigManager.add_listener("_registration_probe", lambda key, value: None)

class Provider:
    name = "_registration_probe"
    key = "_registration_probe"
    priority = 10
    kind = "asr"
    capabilities = frozenset({"image_gen"})
provider = Provider()
register_tts_provider(provider)
register_audio_provider(provider)
register_visual_provider(provider)
register_retrieval_provider(provider)
register_vision_source(provider)
register_audio_source_fetcher("_registration_probe", receive)
ContextProviderRegistry.register(ProviderMeta(name="_registration_probe", provide_fn=receive))
Lifecycle.register("_registration_probe", provider)
'''


@pytest.fixture
def importer(monkeypatch):
    original = hotplug.importlib.import_module
    module = ModuleType(f"{MODULE}.tools")
    declaration = {"suffix": ""}

    def load(name):
        if name != module.__name__:
            return original(name)
        sys.modules[name] = module
        exec(DECLARATIONS + declaration["suffix"], module.__dict__)
        return module

    monkeypatch.setattr(hotplug.importlib, "import_module", load)
    yield declaration, module
    sys.modules.pop(module.__name__, None)


def assert_detached():
    assert EntityRegistry.get(NAME) is None
    assert NAME not in EntityRegistry.list_declared_groups()
    assert "entity/_registration_probe" not in ConfigRegistry.get_all_groups()
    assert NAME not in ConfigManager._listeners
    assert not event_bus.has_listeners(NAME)
    assert NAME not in [meta.name for meta in ContextProviderRegistry.get_all()]
    assert NAME not in [provider.name for provider in get_tts_registry().list()]
    assert NAME not in [provider.name for provider in get_audio_registry().list()]
    assert NAME not in get_visual_router().names()
    assert NAME not in [provider.name for provider in providers.list_providers()]
    assert NAME not in list_source_fetchers()
    assert get_source(NAME) is None
    assert Lifecycle.get(NAME) is None


def test_import_failure_rolls_back_every_registration(importer):
    declaration, _ = importer
    declaration["suffix"] = '''
from core.latebind import LateBinding
port = LateBinding("_registration_probe")
raise RuntimeError("invalid module")
'''
    assert hotplug.load_entity(NAME) is False
    assert_detached()
    assert NAME not in _BINDINGS
    assert f"{MODULE}.tools" not in sys.modules
    assert not ConfigManager.has("_registration_probe_key")


async def test_successful_module_unloads_its_registered_capabilities(importer):
    assert hotplug.load_entity(NAME)
    hotplug._entity_lifecycles[NAME] = {NAME}
    assert EntityRegistry.exists(NAME)
    await hotplug.unload_entity(NAME)
    assert_detached()


def test_failed_module_does_not_roll_back_shared_dependency(monkeypatch, importer):
    declaration, module = importer
    dependency = ModuleType("agent._registration_dependency")
    module.__dict__["dependency"] = dependency
    declaration["suffix"] = '''
exec("from core.latebind import LateBinding\\nport = LateBinding('_shared_dependency_probe')", dependency.__dict__)
raise RuntimeError("fail after shared dependency")
'''
    try:
        assert hotplug.load_entity(NAME) is False
        assert "_shared_dependency_probe" in _BINDINGS
        assert_detached()
    finally:
        _BINDINGS.pop("_shared_dependency_probe", None)


async def test_unload_keeps_shared_dependency_declarations(importer):
    declaration, module = importer
    dependency = ModuleType("agent._registration_dependency")
    module.__dict__["dependency"] = dependency
    declaration["suffix"] = '''
exec("from core.entity import EntityRegistry\\nfrom core.config import register_configs\\nEntityRegistry.register_group('_shared_dependency_probe', 'shared')\\nregister_configs({'shared/probe': {'shared_probe': {'default': True}}})", dependency.__dict__)
'''
    try:
        assert hotplug.load_entity(NAME)
        hotplug._entity_lifecycles[NAME] = {NAME}
        await hotplug.unload_entity(NAME)
        assert EntityRegistry.get_group_description("_shared_dependency_probe") == "shared"
        assert ConfigRegistry.get_item("shared_probe") is not None
    finally:
        EntityRegistry.unregister_group("_shared_dependency_probe")
        ConfigRegistry.unregister_group("shared/probe")


def test_hook_registration_is_undone_on_import_failure(importer):
    declaration, _ = importer
    declaration["suffix"] = '''
from agent.hooks_llm.spec import HookRegistry, LLMHookSpec
HookRegistry.register(LLMHookSpec(name="_registration_probe", event="after_reply", handler=receive))
raise RuntimeError("invalid hook module")
'''
    assert hotplug.load_entity(NAME) is False
    assert HookRegistry.get(NAME) is None
    assert "hooks_llm/_registration_probe" not in ConfigRegistry.get_all_groups()
