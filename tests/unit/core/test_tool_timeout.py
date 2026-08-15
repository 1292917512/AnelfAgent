"""实体层超时判定单元测试。

核心行为：工具自带 timeout 形参（shell/子进程/HTTP 类，内部自行管理执行
超时）时，实体层兜底超时以其生效值（AI 传值或签名默认值）+ 余量为下限，
保证 AI 传入更大的 timeout 不会被外层 wait_for 提前掐断。
"""

from __future__ import annotations

import inspect
import json

import pytest

from core.entity import (
    _INNER_TIMEOUT_MARGIN,
    EntityMetadata,
    EntityRegistry,
    EntityType,
    ToolParam,
    _resolve_inner_timeout,
)


def _metadata(timeout_default: object = None, with_param: bool = True) -> EntityMetadata:
    params = (
        [
            ToolParam(
                name="timeout",
                type="integer",
                required=False,
                default=timeout_default if timeout_default is not None else inspect.Parameter.empty,
            )
        ]
        if with_param
        else []
    )
    return EntityMetadata(
        name="timeout_probe",
        entity_type=EntityType.TOOL,
        description="test",
        meta={"params": params},
    )


class TestResolveInnerTimeout:
    def test_kwargs_value_wins(self) -> None:
        assert _resolve_inner_timeout(_metadata(120), {"timeout": 300}) == 300.0

    def test_signature_default_when_absent(self) -> None:
        assert _resolve_inner_timeout(_metadata(120), {}) == 120.0

    def test_no_timeout_param_returns_zero(self) -> None:
        assert _resolve_inner_timeout(_metadata(with_param=False), {"timeout": 30}) == 0.0

    def test_empty_sentinel_default_returns_zero(self) -> None:
        assert _resolve_inner_timeout(_metadata(), {}) == 0.0

    def test_zero_default_returns_zero(self) -> None:
        """ssh_exec 类语义：默认 0 表示用内部配置缺省，不作为外层依据。"""
        assert _resolve_inner_timeout(_metadata(0), {}) == 0.0

    def test_non_numeric_value_returns_zero(self) -> None:
        assert _resolve_inner_timeout(_metadata(120), {"timeout": "300"}) == 0.0
        assert _resolve_inner_timeout(_metadata(120), {"timeout": True}) == 0.0


class TestInnerTimeoutExtendsBudget:
    """execute_tool 集成：timeout 形参生效值 + 余量抬高外层兜底，短装饰器超时不再掐断。"""

    @pytest.fixture
    def slow_tool(self):
        def probe(timeout: int = 5) -> str:
            import time

            time.sleep(0.3)
            return json.dumps({"ok": True})

        EntityRegistry.register(
            EntityMetadata(
                name="timeout_probe",
                entity_type=EntityType.TOOL,
                description="test",
                func=probe,
                meta={
                    "params": [ToolParam(name="timeout", type="integer", required=False, default=5)],
                    # 装饰器级超时刻意小于函数执行时长：不抬升外层预算必然超时
                    "timeout": 0.1,
                },
            )
        )
        yield
        EntityRegistry.unregister("timeout_probe")

    async def test_signature_default_extends_budget(self, slow_tool: None) -> None:
        """不传 timeout：签名默认 5s + 余量 > 0.1s 装饰器值，0.3s 执行不被掐断。"""
        assert _INNER_TIMEOUT_MARGIN + 5 > 0.1
        result = await EntityRegistry.execute_tool("timeout_probe", "{}")
        assert json.loads(result).get("ok") is True

    async def test_ai_passed_timeout_extends_budget(self, slow_tool: None) -> None:
        result = await EntityRegistry.execute_tool("timeout_probe", '{"timeout": 10}')
        assert json.loads(result).get("ok") is True
