"""AI 实体发现沿用完整执行契约，歧义查询不猜测实体。"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from core.entity import EntityRegistry
from core.tool_registry import tool
from entities.entity_query.tools import get_entity_config, get_entity_status, list_entity_methods


class _QueryOptions(BaseModel):
    mode: Literal["fast", "full"]
    limit: int = 3


def test_discovery_preserves_nested_schema_and_enum() -> None:
    @tool(name="discovery_contract_probe", group="discovery_contract")
    def probe(options: _QueryOptions) -> str:
        return options.mode

    try:
        result = json.loads(list_entity_methods("discovery_contract"))
        method = next(item for item in result["methods"] if item["name"] == "discovery_contract_probe")
        schema = EntityRegistry.get_tool_schema_by_names(["discovery_contract_probe"])[0]["function"]
        assert method == schema
        assert '"fast"' in json.dumps(method["parameters"])
        assert '"limit"' in json.dumps(method["parameters"])
    finally:
        EntityRegistry.unregister("discovery_contract_probe")


def test_ambiguous_entity_and_config_queries_return_candidates() -> None:
    names = ("ambiguity_probe_a", "ambiguity_probe_b")
    for name in names:
        EntityRegistry.register_tool(name=name, func=lambda: "ok", group="ambiguity")
        metadata = EntityRegistry.get(name)
        assert metadata is not None
        metadata.config_group_name = "entity/" + name
    try:
        status = json.loads(get_entity_status("ambiguity_probe"))
        config = json.loads(get_entity_config("ambiguity_probe"))
        assert status["cause"] == config["cause"] == "param"
        assert set(status["candidates"]) == set(names)
        assert set(config["candidates"]) == {"entity/" + name for name in names}
    finally:
        for name in names:
            EntityRegistry.unregister(name)


def test_tag_lookup_suggests_actual_groups_without_aliasing() -> None:
    names = ("tag_lookup_a", "tag_lookup_b", "tag_lookup_disabled")
    for name, group in zip(names, ("test_notes", "test_scheduler", "test_disabled"), strict=True):
        EntityRegistry.register_tool(name=name, func=lambda: "ok", group=group, tags=["test_heartbeat"])
    try:
        EntityRegistry.set_enabled(names[-1], False, persist=False)
        result = json.loads(list_entity_methods("test_heartbeat"))
        assert result["cause"] == "not_found"
        assert result["did_you_mean"] == ["test_notes", "test_scheduler"]
        assert "标签" in result["hint"]
        assert "methods" not in result
        assert json.loads(list_entity_methods("test_notes"))["methods"][0]["name"] == names[0]
    finally:
        for name in names:
            EntityRegistry.unregister(name)
