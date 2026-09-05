"""实体/频道配置 AI 工具（list/get/update_entity_config）单元测试。

覆盖：按分类浏览分组、频道配置组直查（未启用频道无实体实例也可读）、
PASSWORD 掩码、统一 coerce/枚举校验、写入后经变更监听驱动消费方热更。
"""

from __future__ import annotations

import json

from pydantic import Field

from core.config import ConfigManager, register_model_configs
from entities.entity_query.tools import (
    get_entity_config,
    list_config_groups,
    update_entity_config,
)


def _register_demo() -> None:
    from pydantic import BaseModel

    class _DemoCfg(BaseModel):
        enabled: bool = Field(default=False, description="启用")
        token: str = Field(
            default="", description="令牌",
            json_schema_extra={"value_type": "password"})
        level: int = Field(default=1, description="等级")
        mode: str = Field(
            default="a", description="模式",
            json_schema_extra={"value_type": "enum", "options": ["a", "b"]})

    register_model_configs("adapter/eqdemo", _DemoCfg, key_prefix="eqdemo")


class TestListConfigGroups:
    def test_lists_registered_groups(self) -> None:
        _register_demo()
        payload = json.loads(list_config_groups())
        groups = {g["group"]: g["item_count"] for g in payload["groups"]}
        assert groups["adapter/eqdemo"] == 4


class TestGetEntityConfig:
    def test_channel_group_direct_lookup(self) -> None:
        """频道未启用（无实体实例）时经组名直查配置。"""
        _register_demo()
        payload = json.loads(get_entity_config("eqdemo"))
        assert payload["config_group"] == "adapter/eqdemo"
        assert any(i["key"] == "eqdemo_level" for i in payload["config_items"])

    def test_password_masked(self) -> None:
        _register_demo()
        ConfigManager.set("eqdemo_token", "abcd1234efgh5678")
        payload = json.loads(get_entity_config("eqdemo"))
        item = next(i for i in payload["config_items"] if i["key"] == "eqdemo_token")
        assert item["value"] == "abcd****5678"

    def test_unknown_entity_not_found(self) -> None:
        payload = json.loads(get_entity_config("no_such_entity_xyz"))
        assert payload["cause"] == "not_found"


class TestUpdateEntityConfig:
    def test_update_with_coercion(self) -> None:
        _register_demo()
        payload = json.loads(update_entity_config("eqdemo_level", "30"))
        assert payload["success"] is True and payload["new_value"] == 30
        assert ConfigManager.get("eqdemo_level") == 30

    def test_unknown_key_not_found(self) -> None:
        payload = json.loads(update_entity_config("no_such_key_xyz", "1"))
        assert payload["cause"] == "not_found"

    def test_enum_validation(self) -> None:
        _register_demo()
        payload = json.loads(update_entity_config("eqdemo_mode", "zzz"))
        assert payload["cause"] == "param"
        assert ConfigManager.get("eqdemo_mode") == "a"

    def test_bad_type_param_error(self) -> None:
        _register_demo()
        payload = json.loads(update_entity_config("eqdemo_level", "abc"))
        assert payload["cause"] == "param"

    def test_update_notifies_channel_listener(self) -> None:
        """AI 写频道配置 → 变更监听同步通知（频道内存态热更的驱动路径）。"""
        _register_demo()
        fired: list = []
        ConfigManager.add_listener("eqdemo_", lambda k, v: fired.append((k, v)))
        payload = json.loads(update_entity_config("eqdemo_enabled", "true"))
        assert payload["success"] is True
        assert ("eqdemo_enabled", True) in fired

    def test_tool_registered_with_critical_risk(self) -> None:
        from core.entity import EntityRegistry

        entity = EntityRegistry.get("update_entity_config")
        assert entity is not None
        assert entity.meta.get("risk") == "CRITICAL"
