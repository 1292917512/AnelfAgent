"""桥配置与后端离线行为测试（不触 Windows/微信，wechatauto 惰性导入）。"""

from __future__ import annotations

import json

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
