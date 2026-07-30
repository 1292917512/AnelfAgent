"""工具参数类型矫正与校验单元测试。

覆盖：_coerce_param_value/_coerce_kwargs_types 纯函数、execute_tool 集成矫正
（LLM 将纯数字 ID 按 JSON number 传递、布尔值按字符串传递等）、未知参数拦截、
嵌套包装解包，以及字符串数字导致 min/max 比较崩溃的历史 bug 回归。
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


class TestExecuteToolUnknownParams:
    """execute_tool 未知参数拦截：不接收 **kwargs 的工具收到 schema 外参数时，
    应返回含正确参数列表的可行动错误，而非崩溃成 TypeError。"""

    @pytest.fixture
    def strict_tool(self):
        def echo_target(target_id: str) -> str:
            return json.dumps({"ok": True, "target_id": target_id})

        EntityRegistry.register(EntityMetadata(
            name="strict_echo",
            entity_type=EntityType.TOOL,
            description="test",
            func=echo_target,
            meta={"params": [ToolParam(name="target_id", type="string")]},
        ))
        yield
        EntityRegistry.unregister("strict_echo")

    @pytest.fixture
    def kwargs_tool(self):
        def echo_kwargs(**kwargs) -> str:  # type: ignore[no-untyped-def]
            return json.dumps({"ok": True, "kwargs": kwargs})

        EntityRegistry.register(EntityMetadata(
            name="kwargs_echo",
            entity_type=EntityType.TOOL,
            description="test",
            func=echo_kwargs,
            meta={"params": [ToolParam(name="target_id", type="string")]},
        ))
        yield
        EntityRegistry.unregister("kwargs_echo")

    async def test_unknown_param_blocked_with_valid_list(self, strict_tool: None) -> None:
        result = await EntityRegistry.execute_tool(
            "strict_echo", '{"target_id": "1", "chat_id": "2", "channel": "telegram"}',
        )
        payload = json.loads(result)
        assert "error" in payload
        assert payload["valid_params"] == ["target_id"]
        assert "chat_id" in payload["error"] and "channel" in payload["error"]

    async def test_timeout_param_not_treated_as_unknown(self, strict_tool: None) -> None:
        result = await EntityRegistry.execute_tool(
            "strict_echo", '{"target_id": "1", "_timeout": 5}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True

    async def test_kwargs_func_passes_unknown_through(self, kwargs_tool: None) -> None:
        """声明 **kwargs 的工具（MCP 代理/频道 handler）未知参数合法透传。"""
        result = await EntityRegistry.execute_tool(
            "kwargs_echo", '{"target_id": "1", "extra": "x"}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True
        assert payload["kwargs"]["extra"] == "x"


class TestUnwrapNestedArguments:
    """execute_tool 嵌套包装解包：模型先验产生的 {"tool_args": "{...}"} 格式。"""

    @pytest.fixture
    def image_tool(self):
        received: dict = {}

        def recognize(image_path: str = "", **kwargs: str) -> str:
            received["image_path"] = image_path
            return json.dumps({"ok": True, "image_path": image_path})

        EntityRegistry.register(EntityMetadata(
            name="unwrap_image",
            entity_type=EntityType.TOOL,
            description="test",
            func=recognize,
            meta={"params": [ToolParam(name="image_path", type="string", required=False)]},
        ))
        yield received
        EntityRegistry.unregister("unwrap_image")

    async def test_tool_args_wrapper_unwrapped(self, image_tool: dict) -> None:
        result = await EntityRegistry.execute_tool(
            "unwrap_image", '{"tool_args": "{\\"image_path\\": \\"/tmp/a.png\\"}"}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True
        assert image_tool["image_path"] == "/tmp/a.png"

    async def test_dict_wrapper_unwrapped(self, image_tool: dict) -> None:
        result = await EntityRegistry.execute_tool(
            "unwrap_image", '{"arguments": {"image_path": "/tmp/b.png"}}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True
        assert image_tool["image_path"] == "/tmp/b.png"

    async def test_direct_params_untouched(self, image_tool: dict) -> None:
        result = await EntityRegistry.execute_tool(
            "unwrap_image", '{"image_path": "/tmp/c.png"}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True
        assert image_tool["image_path"] == "/tmp/c.png"

    async def test_mixed_declared_and_wrapper_not_unwrapped(self, image_tool: dict) -> None:
        """声明参数与包装键混传时不做猜测，保持原样。"""
        result = await EntityRegistry.execute_tool(
            "unwrap_image", '{"image_path": "/tmp/d.png", "tool_args": "{\\"image_path\\": \\"/x\\"}"}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True
        assert image_tool["image_path"] == "/tmp/d.png"

    async def test_wrapper_preserved_timeout(self, image_tool: dict) -> None:
        result = await EntityRegistry.execute_tool(
            "unwrap_image", '{"tool_args": "{\\"image_path\\": \\"/tmp/e.png\\"}", "_timeout": 30}',
        )
        payload = json.loads(result)
        assert payload.get("ok") is True
        assert image_tool["image_path"] == "/tmp/e.png"


# ==================================================================
# execute_tool 矫正回归（字符串数字导致 min/max 比较崩溃的历史 bug）
# ==================================================================

def _demo(scope_id: str, limit: int = 30, ratio: float = 1.0, flag: bool = False) -> str:
    """测试工具。

    Args:
        scope_id: ID
        limit: 整数
        ratio: 浮点
        flag: 布尔
    """
    return json.dumps({
        "limit": (type(limit).__name__, limit),
        "ratio": (type(ratio).__name__, ratio),
        "flag": (type(flag).__name__, flag),
    }, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _demo_tool():
    EntityRegistry.register_tool(
        name="coercion_demo", func=_demo, group="test",
        params=[
            ToolParam(name="scope_id", type="string"),
            ToolParam(name="limit", type="integer", required=False, default=30),
            ToolParam(name="ratio", type="number", required=False, default=1.0),
            ToolParam(name="flag", type="boolean", required=False, default=False),
        ],
    )
    yield
    EntityRegistry.unregister("coercion_demo")


class TestParamCoercion:
    async def test_string_number_coerced(self) -> None:
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": "1", "limit": "5"}),
        ))
        assert r["limit"] == ["int", 5]

    async def test_float_to_int_coerced(self) -> None:
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": "1", "limit": 5.0}),
        ))
        assert r["limit"] == ["int", 5]

    async def test_string_float_coerced(self) -> None:
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": "1", "ratio": "0.75"}),
        ))
        assert r["ratio"] == ["float", 0.75]

    async def test_string_bool_coerced(self) -> None:
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": "1", "flag": "true"}),
        ))
        assert r["flag"] == ["bool", True]

    async def test_number_id_to_string_coerced(self) -> None:
        # 纯数字 ID 被按 JSON number 传递 → 矫正为字符串
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": 12345}),
        ))
        assert r["limit"] == ["int", 30]  # 默认值不受影响


class TestParamValidation:
    async def test_invalid_int_rejected(self) -> None:
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": "1", "limit": "abc"}),
        ))
        assert "参数类型错误" in r["error"]
        assert "limit" in r["error"]

    async def test_invalid_bool_rejected(self) -> None:
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": "1", "flag": "maybe"}),
        ))
        assert "参数类型错误" in r["error"]

    async def test_valid_passes(self) -> None:
        r = json.loads(await EntityRegistry.execute_tool(
            "coercion_demo", json.dumps({"scope_id": "1", "limit": 10}),
        ))
        assert "error" not in r
