"""wcauto 频道配置测试：默认值安全姿态 / 策略枚举校验。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from channels.wcauto.config import WcautoConfig


class TestDefaults:
    def test_defaults_safe(self) -> None:
        cfg = WcautoConfig()
        assert cfg.enabled is False            # 默认不启用
        assert cfg.allow_write is False        # 敏感写默认关
        assert cfg.require_mention is True     # 群聊默认需 @我
        assert cfg.ws_url.startswith("ws://")
        assert cfg.rpc_timeout >= 30           # 发送含 GUI 操作，超时不能太短
        assert cfg.send_verify is True         # 默认回读校验
        assert cfg.auto_download_image is True
        assert cfg.auto_download_file is False  # 文件默认不自动下载
        assert cfg.recall_notify is True

    def test_policy_validation(self) -> None:
        assert WcautoConfig(dm_policy="allowlist").dm_policy == "allowlist"
        assert WcautoConfig(group_policy="disabled").group_policy == "disabled"
        assert WcautoConfig(dm_policy="OPEN").dm_policy == "open"  # 大小写归一
        with pytest.raises(ValidationError):
            WcautoConfig(dm_policy="everyone")
        with pytest.raises(ValidationError):
            WcautoConfig(group_policy="friends")
