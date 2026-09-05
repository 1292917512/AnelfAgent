"""插件操作状态板测试。"""

import pytest

from core.plugins.status import OperationBoard


class TestOperationBoard:
    def test_active_during_operation(self):
        """操作进行中可见，结束后清空。"""
        board = OperationBoard()
        with board.track("安装", "demo"):
            snap = board.snapshot()
            assert len(snap["active"]) == 1
            assert snap["active"][0]["action"] == "安装"
        assert board.snapshot()["active"] == []

    def test_failure_recorded_on_exception(self):
        """操作异常时记入最近失败并继续抛出。"""
        board = OperationBoard()
        with pytest.raises(RuntimeError), board.track("升级", "demo"):
            raise RuntimeError("git 超时")
        snap = board.snapshot()
        assert snap["active"] == []
        assert snap["failures"][0]["error"] == "git 超时"
        assert snap["failures"][0]["action"] == "升级"

    def test_success_clears_nothing(self):
        """成功操作不产生任何记录（常态零占用）。"""
        board = OperationBoard()
        with board.track("安装", "demo"):
            pass
        snap = board.snapshot()
        assert snap["active"] == [] and snap["failures"] == []

    def test_failure_cap(self):
        """失败记录按上限截断（新的在前）。"""
        board = OperationBoard()
        for i in range(8):
            board.record_failure("安装", f"p{i}", "err")
        failures = board.snapshot()["failures"]
        assert len(failures) == 5
        assert failures[0]["name"] == "p7"

    def test_failure_ttl_expiry(self, monkeypatch):
        """超过保留窗口的失败记录自动过期。"""
        import core.plugins.status as status_mod

        class _FutureClock:
            @staticmethod
            def time() -> float:
                return 1e12

        board = OperationBoard()
        board.record_failure("安装", "demo", "err")
        assert len(board.snapshot()["failures"]) == 1
        monkeypatch.setattr(status_mod, "time", _FutureClock)
        assert board.snapshot()["failures"] == []
