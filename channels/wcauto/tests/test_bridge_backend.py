"""桥配置与后端离线行为测试（不触 Windows/微信，wechatauto 惰性导入）。"""

from __future__ import annotations

import json
import sys
import threading
import time
from types import ModuleType

import pytest

from channels.wcauto.bridge.wcauto_bridge.backend import BridgeError, WeChatBackend
from channels.wcauto.bridge.wcauto_bridge.config import BridgeConfig, load_config


class TestBridgeConfig:
    def test_defaults(self) -> None:
        cfg = BridgeConfig()
        assert cfg.port == 8655
        assert cfg.enable_recall_guard is True
        assert cfg.enable_moments is True
        assert cfg.media_max_bytes == 25 * 1024 * 1024

    def test_load_json(self, tmp_path) -> None:
        path = tmp_path / "bridge.json"
        path.write_text(json.dumps({
            "port": 9000, "token": "abc", "ignore_chats": ["wxid_x", "  "],
            "media_max_mb": 10,
        }), encoding="utf-8")
        cfg = load_config(str(path))
        assert cfg.port == 9000 and cfg.token == "abc"
        assert cfg.ignore_chats == ["wxid_x"]
        assert cfg.media_max_bytes == 10 * 1024 * 1024

    def test_env_override(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "bridge.json"
        path.write_text(json.dumps({"port": 9000}), encoding="utf-8")
        monkeypatch.setenv("WCAUTO_BRIDGE_PORT", "9100")
        monkeypatch.setenv("WCAUTO_BRIDGE_ENABLE_MOMENTS", "false")
        monkeypatch.setenv("WCAUTO_BRIDGE_IGNORE_CHATS", "a@chatroom, b")
        cfg = load_config(str(path))
        assert cfg.port == 9100                    # 环境变量优先于文件
        assert cfg.enable_moments is False
        assert cfg.ignore_chats == ["a@chatroom", "b"]

    def test_missing_file_uses_defaults(self, tmp_path) -> None:
        cfg = load_config(str(tmp_path / "nonexistent.json"))
        assert cfg.port == 8655

    def test_invalid_json_root_rejected(self, tmp_path) -> None:
        path = tmp_path / "bridge.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(ValueError):
            load_config(str(path))


class TestBackendOffline:
    """无 wechatauto 环境下的后端行为：构造可用，依赖微信的调用报明确错误。"""

    def _backend(self) -> WeChatBackend:
        return WeChatBackend(BridgeConfig(), lambda event, data: None)

    def test_unknown_method_rejected(self) -> None:
        with pytest.raises(BridgeError, match="未知 RPC"):
            self._backend().handle_rpc("drop_database", {})

    def test_ping_without_wechat(self) -> None:
        result = self._backend().ping()
        assert result["pong"] is True
        assert result["listener_running"] is False
        assert result["gui_ready"] is False

    def test_download_media_requires_start(self) -> None:
        with pytest.raises(BridgeError, match="未初始化"):
            self._backend().handle_rpc("download_media",
                                       {"chat": "wxid_x", "local_id": 1})

    def test_display_name_passthrough(self) -> None:
        backend = self._backend()
        assert backend._display_name("朋友甲") == "朋友甲"
        with pytest.raises(BridgeError, match="为空"):
            backend._display_name("  ")

    def test_first_concurrent_sends_initialize_once_and_serialize(self, monkeypatch) -> None:
        initialized: list[object] = []
        operations: list[tuple[str, str]] = []
        errors: list[Exception] = []
        done = threading.Event()
        finished: list[str] = []
        finished_lock = threading.Lock()

        class FakeGui:
            def __init__(self) -> None:
                initialized.append(self)
                time.sleep(0.01)

            def send_msg(self, text: str, **kwargs: object) -> dict[str, bool]:
                operations.append(("start", text))
                time.sleep(0.005)
                operations.append(("end", text))
                return {"ok": True}

        gui_module = ModuleType("wechatauto.guia")
        monkeypatch.setattr(gui_module, "WeChatGUI", FakeGui, raising=False)
        monkeypatch.setitem(sys.modules, "wechatauto.guia", gui_module)
        backend = self._backend()

        def send(text: str) -> None:
            try:
                assert backend.send_msg("朋友甲", text) == {"ok": True}
            except Exception as exc:
                errors.append(exc)
            finally:
                with finished_lock:
                    finished.append(text)
                    if len(finished) == 8:
                        done.set()

        workers = [threading.Thread(target=send, args=(str(i),), daemon=True) for i in range(8)]
        for worker in workers:
            worker.start()
        assert done.wait(2), "首次发送或并发发送发生死锁"
        for worker in workers:
            worker.join(timeout=1)
        assert not errors
        assert len(initialized) == 1
        assert backend._get_gui() is initialized[0]
        for start, end in zip(operations[::2], operations[1::2], strict=True):
            assert start == ("start", end[1])
            assert end[0] == "end"


class TestWxResponseHandling:
    def _backend(self) -> WeChatBackend:
        return WeChatBackend(BridgeConfig(), lambda event, data: None)

    def test_send_success_passthrough(self) -> None:
        backend = self._backend()
        backend._gui = type("FakeGui", (), {
            "send_msg": lambda self, text, **kwargs: {
                "status": "成功", "message": "已发送", "data": {"local_id": 1}},
        })()
        result = backend.send_msg("朋友甲", "你好")
        assert result["status"] == "成功"

    def test_send_failure_not_reported_as_success(self) -> None:
        backend = self._backend()
        backend._gui = type("FakeGui", (), {
            "send_msg": lambda self, text, **kwargs: {
                "status": "失败", "message": "微信窗口不可见", "data": None},
        })()
        with pytest.raises(BridgeError, match="微信窗口不可见"):
            backend.send_msg("朋友甲", "你好")

    def test_send_empty_response_rejected(self) -> None:
        backend = self._backend()
        backend._gui = type("FakeGui", (), {
            "send_msg": lambda self, text, **kwargs: None,
        })()
        with pytest.raises(BridgeError, match="无响应"):
            backend.send_msg("朋友甲", "你好")
