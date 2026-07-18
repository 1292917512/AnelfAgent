"""参数类型矫正（core.entity._coerce_kwargs_types + execute_tool 集成）单元测试。

覆盖场景：LLM 将纯数字 ID 按 JSON number 传递、布尔值按字符串传递等
类型错误，在调用工具函数前按 schema 声明类型统一矫正。
"""

from __future__ import annotations

import json

import pytest

from core.entity import (
    EntityMetadata,
    EntityRegistry,
    EntityType,
    ToolParam,
    _coerce_kwargs_types,
    _coerce_param_value,
)


def _params(*specs: tuple) -> list[ToolParam]:
    return [ToolParam(name=name, type=ptype) for name, ptype in specs]


class TestCoerceParamValue:
    def test_string_from_int(self) -> None:
        assert _coerce_param_value(1292917512, "string") == "1292917512"

    def test_string_from_integer_float_strips_dot(self) -> None:
        """整值浮点转字符串应去掉 .0，避免 ID 类参数被污染。"""
        assert _coerce_param_value(1292917512.0, "string") == "1292917512"

    def test_string_from_non_integer_float(self) -> None:
        assert _coerce_param_value(3.14, "string") == "3.14"

    def test_string_from_bool(self) -> None:
        assert _coerce_param_value(True, "string") == "true"
        assert _coerce_param_value(False, "string") == "false"

    def test_string_passthrough(self) -> None:
        assert _coerce_param_value("abc", "string") == "abc"

    def test_integer_from_numeric_string(self) -> None:
        assert _coerce_param_value("42", "integer") == 42
        assert _coerce_param_value(" 42 ", "integer") == 42

    def test_integer_from_non_numeric_string_passthrough(self) -> None:
        assert _coerce_param_value("abc", "integer") == "abc"

    def test_integer_from_integer_float(self) -> None:
        assert _coerce_param_value(3.0, "integer") == 3

    def test_integer_from_non_integer_float_passthrough(self) -> None:
        assert _coerce_param_value(3.5, "integer") == 3.5

    def test_integer_from_bool_passthrough(self) -> None:
        """bool 是 int 子类，integer 分支不得误转 True/False。"""
        assert _coerce_param_value(True, "integer") is True

    def test_number_from_numeric_string(self) -> None:
        assert _coerce_param_value("3.14", "number") == 3.14

    def test_number_from_bool_passthrough(self) -> None:
        assert _coerce_param_value(True, "number") is True

    def test_boolean_from_string(self) -> None:
        assert _coerce_param_value("true", "boolean") is True
        assert _coerce_param_value("TRUE", "boolean") is True
        assert _coerce_param_value("1", "boolean") is True
        assert _coerce_param_value("false", "boolean") is False
        assert _coerce_param_value("0", "boolean") is False

    def test_boolean_from_int(self) -> None:
        assert _coerce_param_value(1, "boolean") is True
        assert _coerce_param_value(0, "boolean") is False

    def test_boolean_from_other_passthrough(self) -> None:
        assert _coerce_param_value(2, "boolean") == 2
        assert _coerce_param_value("yes", "boolean") == "yes"

    def test_array_object_untouched(self) -> None:
        assert _coerce_param_value("not a list", "array") == "not a list"
        assert _coerce_param_value(123, "object") == 123


class TestCoerceKwargsTypes:
    def test_coerce_only_declared_params(self) -> None:
        params = _params(("target_id", "string"), ("count", "integer"))
        kwargs = {"target_id": 1292917512, "count": "5", "extra": 999}
        result = _coerce_kwargs_types(params, kwargs)
        assert result["target_id"] == "1292917512"
        assert result["count"] == 5
        assert result["extra"] == 999, "schema 未声明的参数应原样保留"

    def test_empty_inputs(self) -> None:
        assert _coerce_kwargs_types([], {"a": 1}) == {"a": 1}
        assert _coerce_kwargs_types(_params(("a", "string")), {}) == {}


class TestExecuteToolCoercion:
    """execute_tool 集成：注册声明 string 参数的工具，传 number 应被矫正。"""

    @pytest.fixture
    def registered_tool(self):
        received: dict = {}

        def echo_target(target_id: str) -> str:
            received["target_id"] = target_id
            received["type"] = type(target_id).__name__
            return json.dumps({"ok": True})

        EntityRegistry.register(EntityMetadata(
            name="coerce_echo",
            entity_type=EntityType.TOOL,
            description="test",
            func=echo_target,
            meta={"params": [ToolParam(name="target_id", type="string")]},
        ))
        yield received
        EntityRegistry.unregister("coerce_echo")

    async def test_number_arg_coerced_to_string(self, registered_tool: dict) -> None:
        result = await EntityRegistry.execute_tool(
            "coerce_echo", '{"target_id": 1292917512}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True
        assert registered_tool["target_id"] == "1292917512"
        assert registered_tool["type"] == "str"
