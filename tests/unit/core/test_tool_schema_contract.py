"""声明的参数结构、模型 schema 与执行入口必须一致。"""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from core.entity import EntityRegistry
from core.tool_schema import extract_tool_params


class Target(BaseModel):
    name: str
    count: Annotated[int, Field(ge=1)]


class Mode(str, Enum):
    FAST = "fast"


async def structured_probe(options: list[str] | None, target: Target, mode: Literal["fast", "full"] = "fast") -> str:
    return json.dumps({"options": options, "target": target.model_dump(), "mode": mode})


@pytest.fixture(autouse=True)
def registered_probe():
    EntityRegistry.register_tool(
        name="_schema_probe", func=structured_probe, params=extract_tool_params(structured_probe),
    )
    yield
    EntityRegistry.unregister("_schema_probe")


def test_nested_schema_and_optional_array():
    schema = EntityRegistry.get_tool_schema_by_names(["_schema_probe"])[0]["function"]["parameters"]
    options = schema["properties"]["options"]
    assert options["anyOf"] == [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}]
    assert schema["properties"]["target"]["properties"]["count"]["minimum"] == 1
    assert schema["properties"]["mode"]["enum"] == ["fast", "full"]


def test_nested_definitions_have_resolvable_distinct_parameter_names():
    def batch(first: list[Target], second: dict[str, Target]) -> None:
        pass

    EntityRegistry.register_tool(name="_schema_probe", func=batch, params=extract_tool_params(batch))
    schema = EntityRegistry.get_tool_schema_by_names(["_schema_probe"])[0]["function"]["parameters"]
    for key, inner in (("first", "items"), ("second", "additionalProperties")):
        reference = schema["properties"][key][inner]["$ref"]
        assert reference == f"#/$defs/{key}_Target"
        assert f"{key}_Target" in schema["$defs"]


async def test_valid_nested_value_reaches_typed_handler():
    result = json.loads(await EntityRegistry.execute_tool(
        "_schema_probe", json.dumps({"options": ["a", "b"], "target": {"name": "x", "count": 2}}),
    ))
    assert result["options"] == ["a", "b"]
    assert result["target"] == {"name": "x", "count": 2}


@pytest.mark.parametrize("changes,location", [
    ({"options": "a,b"}, "options"),
    ({"options": [1]}, "options[0]"),
    ({"target": {"name": "x", "count": 0}}, "target[count]"),
    ({"mode": "unknown"}, "mode"),
])
async def test_invalid_nested_value_has_actionable_error(changes, location):
    arguments = {"options": None, "target": {"name": "x", "count": 1}, **changes}
    result = json.loads(await EntityRegistry.execute_tool("_schema_probe", json.dumps(arguments)))
    assert result["cause"] == "param"
    assert location in result["error"]
    assert result["diagnostic"]["call_id"]


def test_ui_options_are_arrays():
    from entities.ui.tools import ui_ask

    options = next(param for param in extract_tool_params(ui_ask) if param.name == "options")
    assert options.type == "array"


async def test_json_collections_and_enums_are_converted_to_declared_python_types():
    async def batch(coordinates: tuple[int, int], flags: set[str], mode: Mode) -> str:
        assert isinstance(coordinates, tuple)
        assert isinstance(flags, set)
        assert mode is Mode.FAST
        return "ok"

    EntityRegistry.register_tool(name="_schema_probe", func=batch, params=extract_tool_params(batch))
    assert await EntityRegistry.execute_tool("_schema_probe", json.dumps({
        "coordinates": [1, 2], "flags": ["a", "a"], "mode": "fast",
    })) == "ok"


def test_different_module_cannot_replace_same_source_tool():
    def intruder() -> str:
        return "wrong"

    intruder.__module__ = "entities.another.tools"
    assert not EntityRegistry.register_tool(name="_schema_probe", func=intruder)
    assert EntityRegistry.get("_schema_probe").func is structured_probe
