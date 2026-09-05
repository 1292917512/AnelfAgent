"""统一配置元数据 API（/api/config/meta）单元测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.config import ConfigItem, ConfigManager, ConfigRegistry


@pytest.fixture
def client():
    from web.server import create_app
    return TestClient(create_app())


@pytest.fixture
def demo_config():
    """注册一个测试配置项并在用后清理。"""
    ConfigRegistry.register(ConfigItem(
        key="meta_test_flag", group="测试组",
        description="测试开关", default_value=True,
    ))
    ConfigManager.set("meta_test_flag", True)
    yield "meta_test_flag"
    ConfigManager.set("meta_test_flag", True)


class TestGetMeta:
    def test_groups_structure(self, client, demo_config) -> None:
        r = client.get("/api/config/meta")
        assert r.status_code == 200
        groups = r.json()["groups"]
        assert isinstance(groups, list) and groups
        test_group = next(g for g in groups if g["group"] == "测试组")
        item = next(i for i in test_group["items"] if i["key"] == demo_config)
        assert item["description"] == "测试开关"
        assert item["type"] == "boolean"
        assert item["value"] is True
        assert item["editable"] is True
        assert item["source"] == "config_manager"

    def test_mind_field_marked(self, client) -> None:
        from agent.config import get_config_provider
        get_config_provider()
        r = client.get("/api/config/meta")
        for g in r.json()["groups"]:
            for item in g["items"]:
                if item["key"] == "max_tool_iterations":
                    assert item["source"] == "mind"
                    return
        pytest.fail("max_tool_iterations 未出现在元数据中")


class TestSaveMeta:
    def test_save_boolean(self, client, demo_config) -> None:
        r = client.put(f"/api/config/meta/{demo_config}", json={"value": False})
        assert r.status_code == 200
        assert r.json()["value"] is False
        assert ConfigManager.get(demo_config) is False

    def test_save_unknown_key_404(self, client) -> None:
        r = client.put("/api/config/meta/no_such_key_xyz", json={"value": 1})
        assert r.status_code == 404

    def test_type_coercion(self, client) -> None:
        ConfigRegistry.register(ConfigItem(
            key="meta_test_int", group="测试组",
            description="整数", default_value=1,
        ))
        ConfigManager.set("meta_test_int", 1)
        r = client.put("/api/config/meta/meta_test_int", json={"value": "42"})
        assert r.status_code == 200
        assert r.json()["value"] == 42

    def test_type_error_400(self, client) -> None:
        ConfigRegistry.register(ConfigItem(
            key="meta_test_int2", group="测试组",
            description="整数", default_value=1,
        ))
        ConfigManager.set("meta_test_int2", 1)
        r = client.put("/api/config/meta/meta_test_int2", json={"value": "abc"})
        assert r.status_code == 400


class TestRangeMeta:
    @pytest.fixture
    def range_config(self):
        """注册一个 RANGE 配置项（int default + 边界）。"""
        ConfigRegistry.register(ConfigItem(
            key="meta_test_range", group="测试组",
            description="比例", default_value=50,
            value_type="range", advanced=True,
            min_value=0, max_value=100, step=1, unit="%",
        ))
        ConfigManager.set("meta_test_range", 50)
        yield "meta_test_range"
        ConfigManager.set("meta_test_range", 50)

    def test_serialize_extended_fields(self, client, range_config) -> None:
        r = client.get("/api/config/meta")
        assert r.status_code == 200
        item = next(
            i
            for g in r.json()["groups"]
            for i in g["items"]
            if i["key"] == range_config
        )
        assert item["type"] == "range"
        assert item["advanced"] is True
        assert (item["min"], item["max"], item["step"]) == (0, 100, 1)
        assert item["unit"] == "%"

    def test_range_keeps_int_type(self, client, range_config) -> None:
        r = client.put(f"/api/config/meta/{range_config}", json={"value": "66"})
        assert r.status_code == 200
        assert r.json()["value"] == 66
        assert isinstance(r.json()["value"], int)

    def test_range_clamped_to_bounds(self, client, range_config) -> None:
        r = client.put(f"/api/config/meta/{range_config}", json={"value": 150})
        assert r.status_code == 200
        assert r.json()["value"] == 100


class TestPasswordMeta:
    @pytest.fixture
    def secret_config(self):
        """注册一个 PASSWORD 配置项并在用后清理。"""
        ConfigRegistry.register(ConfigItem(
            key="meta_test_secret", group="测试组",
            description="密钥", default_value="",
            value_type="password",
        ))
        ConfigManager.set("meta_test_secret", "abcd1234efgh5678")
        yield "meta_test_secret"
        ConfigManager.set("meta_test_secret", "")

    def test_get_masks_secret(self, client, secret_config) -> None:
        r = client.get("/api/config/meta")
        item = next(
            i
            for g in r.json()["groups"]
            for i in g["items"]
            if i["key"] == secret_config
        )
        assert item["type"] == "password"
        assert item["value"] == "abcd****5678"

    def test_put_masked_value_keeps_current(self, client, secret_config) -> None:
        r = client.put(f"/api/config/meta/{secret_config}", json={"value": "abcd****5678"})
        assert r.status_code == 200
        assert r.json().get("unchanged") is True
        assert ConfigManager.get(secret_config) == "abcd1234efgh5678"

    def test_put_new_value_replaces(self, client, secret_config) -> None:
        r = client.put(f"/api/config/meta/{secret_config}", json={"value": "new-token-0001"})
        assert r.status_code == 200
        assert ConfigManager.get(secret_config) == "new-token-0001"
