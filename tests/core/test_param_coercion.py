"""工具参数类型矫正与校验（EntityRegistry.execute_tool）单元测试。

复现历史 bug：AI 传字符串数字（"limit":"5"）导致 min/max 比较崩溃
（'<' not supported between instances of 'int' and 'str'）。
"""

from __future__ import annotations

import json

import pytest

from core.entity import EntityRegistry, ToolParam


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
