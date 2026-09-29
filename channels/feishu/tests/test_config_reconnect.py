"""飞书频道凭证热切换测试（不触网）。

回归背景：修改 app_id/app_secret 后基类只重新物化配置对象，lark.Client
与 WS 连接不重建——「切换账号」需要手动 toggle 两次才生效。现在凭证字段
变更经去抖调度自动 stop→start 重连。
"""

from __future__ import annotations

import asyncio

import pytest

from agent.channel.channel_types import ChannelStatus
from channels.feishu import state as feishu_state
from channels.feishu.adapter import FeishuChannel
from channels.feishu.users import UserNameCache


@pytest.fixture()
def channel(tmp_path, monkeypatch) -> FeishuChannel:
    """隔离数据目录的频道实例。"""
    monkeypatch.setattr(feishu_state, "feishu_data_dir", lambda: str(tmp_path))
    return FeishuChannel()


def _patch_lifecycle(channel: FeishuChannel) -> list[str]:
    """替换 stop/start 为记录桩，返回调用序列容器。"""
    calls: list[str] = []

    async def fake_stop() -> None:
        calls.append("stop")
        channel._status = ChannelStatus.STOPPED

    async def fake_start() -> None:
        calls.append("start")
        channel._status = ChannelStatus.RUNNING

    channel.stop = fake_stop  # type: ignore[method-assign]
    channel.start = fake_start  # type: ignore[method-assign]
    return calls


class TestCredentialHotSwitch:
    def test_credential_change_triggers_reconnect(self, channel: FeishuChannel) -> None:
        async def scenario() -> list[str]:
            channel._status = ChannelStatus.RUNNING
            channel._main_loop = asyncio.get_running_loop()
            calls = _patch_lifecycle(channel)
            channel._on_config_changed("feishu_app_id", "cli_new")
            await asyncio.sleep(1.3)  # 越过 1s 去抖
            return calls

        assert asyncio.run(scenario()) == ["stop", "start"]

    def test_connection_mode_triggers_reconnect(self, channel: FeishuChannel) -> None:
        async def scenario() -> list[str]:
            channel._status = ChannelStatus.RUNNING
            channel._main_loop = asyncio.get_running_loop()
            calls = _patch_lifecycle(channel)
            channel._on_config_changed("feishu_connection_mode", "webhook")
            await asyncio.sleep(1.3)
            return calls

        assert asyncio.run(scenario()) == ["stop", "start"]

    def test_non_credential_change_skips_reconnect(self, channel: FeishuChannel) -> None:
        async def scenario() -> list[str]:
            channel._status = ChannelStatus.RUNNING
            channel._main_loop = asyncio.get_running_loop()
            calls = _patch_lifecycle(channel)
            channel._on_config_changed("feishu_require_mention", False)
            await asyncio.sleep(1.3)
            return calls

        assert asyncio.run(scenario()) == []

    def test_batched_credential_changes_debounce_to_one_reconnect(self, channel: FeishuChannel) -> None:
        """配置中心连续保存 app_id + app_secret 只触发一次重连。"""

        async def scenario() -> list[str]:
            channel._status = ChannelStatus.RUNNING
            channel._main_loop = asyncio.get_running_loop()
            calls = _patch_lifecycle(channel)
            channel._on_config_changed("feishu_app_id", "cli_new")
            channel._on_config_changed("feishu_app_secret", "secret_new")
            await asyncio.sleep(1.3)
            return calls

        assert asyncio.run(scenario()) == ["stop", "start"]

    def test_stopped_channel_does_not_reconnect(self, channel: FeishuChannel) -> None:
        async def scenario() -> list[str]:
            channel._status = ChannelStatus.STOPPED
            channel._main_loop = asyncio.get_running_loop()
            calls = _patch_lifecycle(channel)
            channel._on_config_changed("feishu_app_id", "cli_new")
            await asyncio.sleep(1.3)
            return calls

        assert asyncio.run(scenario()) == []


class TestStopStateReset:
    def test_stop_clears_runtime_caches(self, channel: FeishuChannel) -> None:
        async def scenario() -> None:
            channel._on_chat_seen("oc_x", "p2p", "ou_peer")
            channel._user_names._denied = True
            await channel.stop()

        asyncio.run(scenario())
        assert channel._known_chats == {}
        assert channel._user_names._denied is False
        assert isinstance(channel._user_names, UserNameCache)


class TestStatusInfoAppIdentity:
    def test_status_info_exposes_app_id(self, channel: FeishuChannel) -> None:
        channel.config.app_id = "cli_a1234567890"
        info = channel.get_status_info()
        assert info["app_id"] == "cli_a1234567890"
        assert info["app_id_tail"] == "567890"
