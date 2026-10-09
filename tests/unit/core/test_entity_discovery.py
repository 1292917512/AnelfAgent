"""实体 API 发现不得执行实例的属性或描述符。"""

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
