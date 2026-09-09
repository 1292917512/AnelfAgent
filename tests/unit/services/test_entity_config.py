"""实体配置读写服务测试：分组名解析与类型矫正（复现历史 bug——
分组实体的配置更新曾全部 400「实体或配置项不存在」，且值不做类型矫正）。"""

from __future__ import annotations

import pytest

from core.config import ConfigManager, register_configs_safe
from core.entity import EntityRegistry
from services.entity import EntityService


@pytest.fixture()
def svc() -> EntityService:
    return EntityService()


@pytest.fixture()
def group_entity():
    """注册一个仅分组可见的实体（工具名 != 组名，无同名实体）。"""
    EntityRegistry.register_tool(
        name="svc_demo_tool", func=lambda: "ok", group="svc_demo",
    )
    register_configs_safe({
        "entity/svc_demo": {
            "svc_demo_enabled": {"description": "开关", "default": True},
            "svc_demo_count": {"description": "数量", "default": 5,
                               "min": 1, "max": 100},
        },
    })
    yield "svc_demo"
    EntityRegistry.unregister("svc_demo_tool")


class TestGroupNameResolution:
    """分组实体（EntityRegistry.get(组名) 为 None）的配置读写应正常工作。"""

    def test_get_config_by_group_name(self, svc: EntityService, group_entity: str) -> None:
        config = svc.get_entity_config(group_entity)
        assert config is not None
        keys = {item["key"] for item in config["items"]}
        assert "svc_demo_enabled" in keys

    def test_update_config_by_group_name(self, svc: EntityService, group_entity: str) -> None:
        assert svc.update_entity_config(group_entity, "svc_demo_enabled", False) is True
        assert ConfigManager.get("svc_demo_enabled") is False

    def test_batch_update_by_group_name(self, svc: EntityService, group_entity: str) -> None:
        count = svc.update_entity_config_batch(
            group_entity,
            {"svc_demo_enabled": False, "nonexistent_key": 1},
        )
        assert count == 1  # 不存在的键被跳过
        assert ConfigManager.get("svc_demo_enabled") is False

    def test_unknown_entity_fails(self, svc: EntityService) -> None:
        assert svc.update_entity_config("no_such_entity", "svc_demo_enabled", True) is False
        assert svc.get_entity_config("no_such_entity") is None


class TestValueCoercion:
    """写入值经配置项声明类型矫正与边界收敛（不存裸字符串）。"""

    def test_string_bool_coerced(self, svc: EntityService, group_entity: str) -> None:
        assert svc.update_entity_config(group_entity, "svc_demo_enabled", "false") is True
        assert ConfigManager.get("svc_demo_enabled") is False

    def test_string_int_coerced_and_clamped(self, svc: EntityService, group_entity: str) -> None:
        assert svc.update_entity_config(group_entity, "svc_demo_count", "42") is True
        assert ConfigManager.get("svc_demo_count") == 42
        assert svc.update_entity_config(group_entity, "svc_demo_count", "999") is True
        assert ConfigManager.get("svc_demo_count") == 100  # clamp 到上界

    def test_unknown_key_fails(self, svc: EntityService, group_entity: str) -> None:
        assert svc.update_entity_config(group_entity, "svc_demo_nope", 1) is False
