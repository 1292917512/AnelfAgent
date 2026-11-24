"""实体发现的静态扫描、热插拔与索引一致性。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

from core.entity import BaseEntity, EntityRegistry


def test_api_discovery_is_static_and_keeps_all_methods() -> None:
    reads: list[str] = []

    class Descriptor:
        def __get__(self, obj: Any, owner: Any = None) -> Any:
            reads.append("descriptor")
            raise RuntimeError("resource not initialized")

    class Entity(BaseEntity):
        b_descriptor = Descriptor()

        @property
        def a_property(self) -> str:
            reads.append("property")
            raise NotImplementedError

        def method(self) -> None:
            pass

        @staticmethod
        def static_api() -> None:
            pass

        @classmethod
        def class_api(cls) -> None:
            pass

        async def async_api(self) -> None:
            pass

    entity = Entity()
    try:
        assert reads == []
        names = {name.rsplit(".", 1)[1] for name in entity.get_registered_apis()}
        assert names == {"method", "static_api", "class_api", "async_api"}
        assert EntityRegistry.activate_entity(entity.get_entity_name())
        assert len(entity.get_registered_apis()) == 4
    finally:
        EntityRegistry.unregister(entity.get_entity_name())


def test_discovery_indexes_survive_concurrent_group_reload() -> None:
    group = "test_discovery_reload"
    name = "test_discovery_reload_tool"
    ready = Barrier(2, timeout=10)

    def tool() -> str:
        return "ok"

    def reload_group() -> None:
        ready.wait()
        for _ in range(80):
            assert EntityRegistry.register_tool(name, tool, "tagged", group=group, tags=[group], catalog_brief=group)
            assert EntityRegistry.register_tool(name, tool, "untagged", group=group, catalog_brief=group)
            assert EntityRegistry.unregister_group(group) == 1

    def read_indexes() -> None:
        ready.wait()
        for _ in range(100):
            for entry in EntityRegistry.get_entity_catalog():
                if entry["group"] == group:
                    assert entry["tool_count"] == 1
                    assert entry["tool_briefs"] == (group,)
            assert all(e.name == name for e in EntityRegistry.search(group))
            assert all(e.name == name for e in EntityRegistry.get_by_tag(group))
            schemas = EntityRegistry.get_tool_schema_by_tags([group])
            assert all(s["function"]["description"] == "tagged" for s in schemas)
            stats = EntityRegistry.get_statistics()
            assert stats["total_entities"] == sum(stats["types"].values())
            summary = EntityRegistry.get_summary()
            assert summary["total"] == sum(t["count"] for t in summary["types"].values())

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reload_group), pool.submit(read_indexes)]
            for future in futures:
                future.result(timeout=30)
        assert not any(entry["group"] == group for entry in EntityRegistry.get_entity_catalog())
        assert not EntityRegistry.get_by_tag(group)
    finally:
        EntityRegistry.unregister_group(group)


def test_old_tool_schema_cannot_overwrite_reloaded_tool() -> None:
    name = "test_discovery_schema_reload"

    def tool() -> str:
        return "ok"

    try:
        assert EntityRegistry.register_tool(name, tool, "original")
        original = EntityRegistry.get(name)
        assert original is not None
        assert EntityRegistry.register_tool(name, tool, "replacement")
        EntityRegistry._get_tool_schema(original)
        schema, = EntityRegistry.get_tool_schema_by_names([name])
        assert schema["function"]["description"] == "replacement"
    finally:
        EntityRegistry.unregister(name)
