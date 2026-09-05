"""core/config.py 统一配置系统增强的单元测试。

覆盖：register_model_configs（pydantic 模型 → ConfigItem 派生）、
ConfigManager 变更监听器、ConfigItem.coerce_value / mask_secret。
"""

from __future__ import annotations

from typing import Literal, Optional

import pytest
from pydantic import Field

from core.config import (
    ConfigItem,
    ConfigManager,
    ConfigRegistry,
    ConfigValueType,
    mask_secret,
    register_model_configs,
)


class TestMaskSecret:
    def test_long_value_keeps_head_tail(self) -> None:
        assert mask_secret("abcdef1234567890") == "abcd****7890"

    def test_short_value_fully_masked(self) -> None:
        assert mask_secret("short") == "****"

    def test_empty_passthrough(self) -> None:
        assert mask_secret("") == ""


class TestCoerceValue:
    def _item(self, value_type: str, default: object = "") -> ConfigItem:
        return ConfigItem(
            key="k", group="g", description="d",
            default_value=default, value_type=value_type,
        )

    def test_boolean_strings(self) -> None:
        item = self._item("boolean", False)
        assert item.coerce_value("true") is True
        assert item.coerce_value("0") is False
        assert item.coerce_value(True) is True

    def test_integer(self) -> None:
        assert self._item("integer", 0).coerce_value("42") == 42
        with pytest.raises(ValueError):
            self._item("integer", 0).coerce_value("abc")

    def test_range_keeps_int_default_type(self) -> None:
        item = self._item("range", 5)
        assert item.coerce_value("7") == 7
        assert isinstance(item.coerce_value("7"), int)

    def test_enum_passthrough_str(self) -> None:
        assert self._item("enum").coerce_value("x") == "x"


class DemoModel:
    pass


class TestRegisterModelConfigs:
    def test_derivation_and_prefixing(self) -> None:
        from pydantic import BaseModel

        class _Model(BaseModel):
            enabled: bool = Field(default=False, description="开关")
            token: str = Field(
                default="", description="令牌",
                json_schema_extra={"value_type": "password"})
            mode: Literal["a", "b"] = Field(default="a", description="模式")
            ratio: float = Field(default=0.5, description="比率")
            note: Optional[str] = Field(default=None, description="备注")
            untouched: str = Field(default="x", description="不注册")

        keys = register_model_configs(
            "adapter/demo", _Model,
            key_prefix="demo",
            only_fields={"enabled", "token", "mode", "ratio", "note"},
        )
        assert keys == ["demo_enabled", "demo_token", "demo_mode", "demo_ratio", "demo_note"]

        assert ConfigRegistry.get_item("demo_enabled").value_type == ConfigValueType.BOOLEAN  # type: ignore[union-attr]
        token = ConfigRegistry.get_item("demo_token")
        assert token is not None and token.is_secret
        mode = ConfigRegistry.get_item("demo_mode")
        assert mode is not None
        assert mode.type_name == "enum" and mode.enum_options == ["a", "b"]
        ratio = ConfigRegistry.get_item("demo_ratio")
        assert ratio is not None and ratio.value_type == ConfigValueType.FLOAT
        assert ConfigRegistry.get_item("demo_untouched") is None

        # 种子默认值已写入 ConfigManager
        assert ConfigManager.get("demo_enabled") is False
        assert ConfigManager.get("demo_mode") == "a"

    def test_existing_value_not_overwritten_by_default(self) -> None:
        from pydantic import BaseModel

        class _Model(BaseModel):
            threshold: int = Field(default=10, description="阈值")

        ConfigManager.set("demo2_threshold", 99)
        register_model_configs("adapter/demo2", _Model, key_prefix="demo2")
        assert ConfigManager.get("demo2_threshold") == 99


class TestConfigListeners:
    def test_set_notifies_prefix_match(self) -> None:
        fired: list = []
        ConfigManager.add_listener("qq_", lambda k, v: fired.append((k, v)))
        ConfigManager.set("qq_enabled", True)
        ConfigManager.set("other_key", 1)
        assert fired == [("qq_enabled", True)]

    def test_add_listener_idempotent(self) -> None:
        fired: list = []

        def cb(k: str, v: object) -> None:
            fired.append(k)

        ConfigManager.add_listener("x_", cb)
        ConfigManager.add_listener("x_", cb)
        ConfigManager.set("x_a", 1)
        assert fired == ["x_a"]

    def test_remove_listener(self) -> None:
        fired: list = []
        cb = lambda k, v: fired.append(k)  # noqa: E731
        ConfigManager.add_listener("y_", cb)
        ConfigManager.remove_listener("y_", cb)
        ConfigManager.set("y_a", 1)
        assert fired == []

    def test_update_notifies_per_key(self) -> None:
        fired: list = []
        ConfigManager.add_listener("z_", lambda k, v: fired.append(k))
        ConfigManager.update({"z_a": 1, "z_b": 2})
        assert fired == ["z_a", "z_b"]

    def test_listener_error_does_not_break_set(self) -> None:
        def _boom(k: str, v: object) -> None:
            raise RuntimeError("exploded")

        ConfigManager.add_listener("err_", _boom)
        ConfigManager.set("err_a", 1)  # 不抛异常
        assert ConfigManager.get("err_a") == 1


class _MemoryStore:
    """ConfigStore 内存替身（协议同名方法即可，无需继承）。"""

    def __init__(self) -> None:
        self.data: dict = {}
        self.saved = 0
        self.loaded = 0

    def load(self) -> None:
        self.loaded += 1

    def get(self, key: str, default: object = None) -> object:
        return self.data.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.data[key] = value

    def has(self, key: str) -> bool:
        return key in self.data

    def save(self) -> None:
        self.saved += 1


class TestConfigStores:
    def test_register_store_loads_and_routes(self) -> None:
        store = _MemoryStore()
        store.data["st_a"] = 1
        ConfigManager.register_store("st_", store)
        assert store.loaded == 1
        assert ConfigManager.get("st_a") == 1
        assert ConfigManager.has("st_a") is True
        assert ConfigManager.has("other") is False

    def test_set_routes_to_store_and_notifies(self) -> None:
        store = _MemoryStore()
        ConfigManager.register_store("st_", store)
        fired: list = []
        ConfigManager.add_listener("st_", lambda k, v: fired.append((k, v)))
        ConfigManager.set("st_b", 2)
        assert store.data == {"st_b": 2}
        assert "st_b" not in ConfigManager.get_all()  # 不进 app_config 内存层
        assert fired == [("st_b", 2)]

    def test_save_flushes_stores(self) -> None:
        store = _MemoryStore()
        ConfigManager.register_store("st_", store)
        ConfigManager.save()
        assert store.saved == 1

    def test_update_routes_per_key(self) -> None:
        store = _MemoryStore()
        ConfigManager.register_store("st_", store)
        ConfigManager.update({"st_x": 1, "plain": 2})
        assert store.data == {"st_x": 1}
        assert ConfigManager.get("plain") == 2
