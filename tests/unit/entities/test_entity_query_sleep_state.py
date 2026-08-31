"""实体自省工具的沉睡状态标注（entities.entity_query.tools）单元测试。

锁定：list_entity_methods 对可沉睡分组输出 sleeping 状态与激活指引，
避免 AI 误以为沉睡方法可直接调用而陷入"发现→调用失败→再发现"死循环。
"""

from __future__ import annotations

import json

import pytest

from agent.mind.tool_activation import bind_scope, reset_scope, tool_activation
from core.config import ConfigManager
from core.entity import EntityRegistry


def _register(group: str, name: str, **kwargs) -> None:
    EntityRegistry.register_tool(name=name, func=lambda: "ok", group=group, **kwargs)


class TestListEntityMethodsSleepState:
    def test_sleeping_group_marked_with_hint(self) -> None:
        from entities.entity_query.tools import list_entity_methods

        _register("lq_sleepg", "lq_sleepy", allow_sleep=True, sleep_brief="简介")
        try:
            result = json.loads(list_entity_methods(group="lq_sleepg"))
            assert result["sleeping"] is True
            assert 'activate_tool_group(group="lq_sleepg")' in result["hint"]
            assert any(m["name"] == "lq_sleepy" for m in result["methods"])
        finally:
            EntityRegistry.unregister("lq_sleepy")

    def test_activated_group_reports_rounds_left(self) -> None:
        from entities.entity_query.tools import list_entity_methods

        _register("lq_actg", "lq_awake", allow_sleep=True, sleep_brief="简介")
        token = bind_scope("lq_scope")
        try:
            tool_activation.activate("lq_actg", rounds=3, scope="lq_scope")
            result = json.loads(list_entity_methods(group="lq_actg"))
            assert result["sleeping"] is False
            assert result["active_rounds_left"] == 3
            assert "hint" not in result
        finally:
            reset_scope(token)
            EntityRegistry.unregister("lq_awake")
            tool_activation.clear_scope("lq_scope")

    def test_non_sleepable_group_unchanged_payload(self) -> None:
        from entities.entity_query.tools import list_entity_methods

        _register("lq_plain_g", "lq_plain_t")
        try:
            result = json.loads(list_entity_methods(group="lq_plain_g"))
            assert "sleeping" not in result
            assert "active_rounds_left" not in result
            assert "hint" not in result
        finally:
            EntityRegistry.unregister("lq_plain_t")

    def test_unknown_group_error_lists_available(self) -> None:
        from entities.entity_query.tools import list_entity_methods

        result = json.loads(list_entity_methods(group="no_such_group_xyz"))
        assert "error" in result
        assert "available_groups" in result


class TestToggleEntityGroupPersistence:
    """toggle_entity_group 与 Web 开关同路径：状态写 entity_states 持久化，重启后保持。"""

    @pytest.fixture(autouse=True)
    def _clean_states(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ConfigManager, "save", classmethod(lambda cls: True))
        old = ConfigManager.get("entity_states")
        ConfigManager.set("entity_states", {})
        yield
        ConfigManager.set("entity_states", old or {})

    def test_toggle_persists_entity_states(self) -> None:
        from entities.entity_query.tools import toggle_entity_group

        _register("lq_tog_g", "lq_tog_t")
        try:
            result = json.loads(toggle_entity_group(group="lq_tog_g", enabled=False))
            assert result["success"] is True
            assert result["affected_count"] == 1
            states = ConfigManager.get("entity_states", {})
            assert states.get("lq_tog_t") is False

            result = json.loads(toggle_entity_group(group="lq_tog_g", enabled=True))
            assert result["affected_count"] == 1
            states = ConfigManager.get("entity_states", {})
            assert states.get("lq_tog_t") is True
        finally:
            EntityRegistry.unregister("lq_tog_t")
