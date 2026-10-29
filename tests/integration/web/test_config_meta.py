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
    def test_environment_value_is_explicit_and_readonly(self, client, demo_config, monkeypatch) -> None:
        monkeypatch.setenv("ANELF_META_TEST_FLAG", "false")
        ConfigManager.set(demo_config, True)
        groups = client.get("/api/config/meta").json()["groups"]
        item = next(item for group in groups for item in group["items"] if item["key"] == demo_config)
        assert item["value"] is False
        assert item["editable"] is False
        assert item["environment_variable"] == "ANELF_META_TEST_FLAG"
        assert item["value_source"] == "environment"
        response = client.put(f"/api/config/meta/{demo_config}", json={"value": True})
        assert response.status_code == 409
        assert ConfigManager.get(demo_config) is False

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
    def test_iteration_limit_defaults_to_unlimited_and_can_be_restored(self, client, tmp_path, monkeypatch) -> None:
        import json

        import agent.config as config

        path = tmp_path / "mind.json"
        ConfigManager.set("mind_config_path", str(path))
        monkeypatch.setattr(config, "_provider", None)
        mind = config.get_config_provider().mind
        groups = client.get("/api/config/meta").json()["groups"]
        item = next(item for group in groups for item in group["items"] if item["key"] == "max_tool_iterations")
        assert item["value"] == item["default"] == mind.max_tool_iterations == 0
        assert item["min"] == 0 and item["max"] is None and item["type"] == "integer"
        assert "0 = 不设上限" in item["description"]

        for value in (12, 0):
            response = client.put("/api/config/meta/max_tool_iterations", json={"value": value})
            assert response.status_code == 200 and response.json()["value"] == value
            assert mind.max_tool_iterations == value
            assert json.loads(path.read_text(encoding="utf-8"))["max_tool_iterations"] == value
            config.get_config_provider().reload()
            assert mind.max_tool_iterations == value

    def test_autonomous_interval_is_editable_without_changing_heartbeat(self, client, tmp_path, monkeypatch) -> None:
        import json

        import agent.config as config

        path = tmp_path / "mind.json"
        path.write_text(json.dumps({"heartbeat_interval": 600.0}), encoding="utf-8")
        ConfigManager.set("mind_config_path", str(path))
        monkeypatch.setattr(config, "_provider", None)
        mind = config.get_config_provider().mind

        groups = client.get("/api/config/meta").json()["groups"]
        item = next(item for group in groups for item in group["items"]
                    if item["key"] == "autonomous_decision_interval")
        assert item["value"] == item["default"] == 3600.0
        assert item["source"] == "mind" and item["min"] == 60 and item["editable"]

        for value, expected in ((7200, 7200.0), (1, 60.0)):
            response = client.put("/api/config/meta/autonomous_decision_interval", json={"value": value})
            assert response.status_code == 200 and response.json()["value"] == expected
            assert mind is config.get_config_provider().mind
            assert mind.autonomous_decision_interval == expected
            assert mind.heartbeat_interval == 600.0
            saved = json.loads(path.read_text(encoding="utf-8"))
            assert saved["autonomous_decision_interval"] == expected
            assert saved["heartbeat_interval"] == 600.0

    def test_profile_history_is_shared_with_ai_config_tools(self, client) -> None:
        import json

        from agent.memory import profiles
        from entities.entity_query.tools import get_entity_config, update_entity_config

        assert profiles.ProfileWriter is not None
        response = client.put("/api/config/meta/profile_history_days", json={"value": 14})
        assert response.status_code == 200
        ai_items = json.loads(get_entity_config("memory/profile_history"))["config_items"]
        assert next(item for item in ai_items if item["key"] == "profile_history_days")["value"] == 14
        assert json.loads(update_entity_config("profile_history_days", "30"))["success"]
        group = next(group for group in client.get("/api/config/meta").json()["groups"] if group["group"] == "memory/profile_history")
        item = next(item for item in group["items"] if item["key"] == "profile_history_days")
        assert item["value"] == item["default"] == 30 and item["min"] == 1

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
        assert "new-token-0001" not in r.text
        assert r.json()["value"] == "new-****0001"
