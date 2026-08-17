"""LateBinding 晚绑定端口单元测试。"""
from __future__ import annotations

from typing import Optional

import pytest

from core.latebind import LateBinding as Port
from core.latebind import WireError, assert_wired, reset_all


@pytest.fixture(autouse=True)
def _clean_registry():
    """用例结束后复位，隔离端口注册表的全局副作用。"""
    yield
    reset_all()


def _fresh(name: str) -> Port[int]:
    """创建独立命名的测试端口。"""
    return Port(name)


class TestLateBinding:
    def test_get_before_set_raises_wire_error(self) -> None:
        port = _fresh("test.unwired")
        assert not port.bound
        with pytest.raises(WireError) as exc_info:
            port.get()
        assert exc_info.value.name == "test.unwired"
        assert "test.unwired" in str(exc_info.value)

    def test_set_get_roundtrip(self) -> None:
        port = _fresh("test.roundtrip")
        port.set(42)
        assert port.bound
        assert port.get() == 42

    def test_none_is_valid_bound_value(self) -> None:
        """可选后端以 None 施绑：bound 标志即事实，None 不等于未施绑。"""
        port: Port[Optional[int]] = Port("test.optional")
        port.set(None)
        assert port.bound
        assert port.get() is None
        assert "test.optional" not in assert_wired()

    def test_rebind_replaces_value(self) -> None:
        port = _fresh("test.rebind")
        port.set(1)
        port.set(2)
        assert port.get() == 2

    def test_unbind_resets_to_unwired(self) -> None:
        port = _fresh("test.unbind")
        port.set(1)
        port.unbind()
        assert not port.bound
        with pytest.raises(WireError):
            port.get()

    def test_duplicate_name_rejected(self) -> None:
        Port("test.duplicate")
        with pytest.raises(ValueError, match="名称冲突"):
            Port("test.duplicate")

    def test_name_property(self) -> None:
        assert _fresh("test.named").name == "test.named"


class TestRegistryHelpers:
    def test_assert_wired_lists_only_unbound(self) -> None:
        """已施绑端口不出现在清单中，未施绑的保留（注册表进程级共享，
        仅对用例自有端口做成员断言）。"""
        bound = _fresh("test.a")
        unbound = _fresh("test.b")
        bound.set(1)
        wired = assert_wired()
        assert bound.name not in wired
        assert unbound.name in wired

    def test_reset_all_unbinds_everything(self) -> None:
        p1 = _fresh("test.c")
        p2 = _fresh("test.d")
        p1.set(1)
        p2.set(2)
        reset_all()
        assert not p1.bound
        assert not p2.bound
