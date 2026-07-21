"""BaseChannel v2 抽象基类测试。"""

import pytest

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import AdapterChannel, ChannelType
from agent.channel.schemas import (
    ChannelInfo,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SendRequest,
    SendResponse,
    SendSegment,
)


class DummyConfig(ChannelConfig):
    """测试用配置。"""


class DummyChannel(BaseChannel[DummyConfig]):
    """测试用频道（最简实现）。"""

    channel_id = "dummy"
    display_name = "Dummy"
    capabilities = {ChannelCapability.SEND_TEXT}
    metadata = ChannelMetadata(name="Dummy", description="Test", version="1.0.0")
    _Configs = DummyConfig

    async def start(self) -> None:
        self._status = ChannelStatus.RUNNING

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED

    async def forward_message(self, request: SendRequest) -> SendResponse:
        return SendResponse(success=True, message_id="test-123")

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id="dummy_bot",
            user_name="Dummy Bot",
            is_bot=True,
        )

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        return ChannelUser(platform=self.channel_id, user_id=user_id, user_name=user_id)

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(
            channel_id=channel_id,
            channel_name="Dummy Channel",
            channel_type=ChannelType.PRIVATE,
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail="OK")


@pytest.mark.asyncio
async def test_channel_instantiation():
    """测试频道实例化。"""
    ch = DummyChannel()
    assert ch.channel_id == "dummy"
    assert ch.display_name == "Dummy"
    assert ChannelCapability.SEND_TEXT in ch.capabilities
    assert ch.metadata.name == "Dummy"
    assert ch.metadata.version == "1.0.0"


@pytest.mark.asyncio
async def test_channel_lifecycle():
    """测试频道生命周期。"""
    ch = DummyChannel()
    assert ch.status == ChannelStatus.STOPPED

    await ch.start()
    assert ch.status == ChannelStatus.RUNNING

    await ch.stop()
    assert ch.status == ChannelStatus.STOPPED


@pytest.mark.asyncio
async def test_forward_message():
    """测试统一发送入口。"""
    ch = DummyChannel()
    req = SendRequest(
        adapter_key="dummy",
        channel=AdapterChannel(channel_id="test", channel_type=ChannelType.PRIVATE),
        segments=[SendSegment(type="text", content="Hello")],
    )
    resp = await ch.forward_message(req)
    assert resp.success
    assert resp.message_id == "test-123"


@pytest.mark.asyncio
async def test_send_text_convenience():
    """测试 send_text 便捷方法（内部走 forward_message）。"""
    ch = DummyChannel()
    result = await ch.send_text("test", "Hello")
    import json
    data = json.loads(result)
    assert data["success"] is True


@pytest.mark.asyncio
async def test_info_queries():
    """测试信息查询。"""
    ch = DummyChannel()

    me = await ch.get_self_info()
    assert me.platform == "dummy"
    assert me.is_bot is True

    user = await ch.get_user_info("user_1", "channel_1")
    assert user.user_id == "user_1"

    channel = await ch.get_channel_info("channel_1")
    assert channel.channel_id == "channel_1"
    assert channel.channel_type == ChannelType.PRIVATE


@pytest.mark.asyncio
async def test_health_check():
    """测试健康探针。"""
    ch = DummyChannel()
    health = await ch.check_health()
    assert health.healthy is True
    assert health.latency_ms is not None


@pytest.mark.asyncio
async def test_config_loading():
    """测试配置加载。"""
    ch = DummyChannel()
    cfg = ch.get_config()
    assert isinstance(cfg, DummyConfig)
    assert cfg.enabled is True  # 默认值


@pytest.mark.asyncio
async def test_get_status_info():
    """测试状态信息。"""
    ch = DummyChannel()
    info = ch.get_status_info()
    assert info["key"] == "dummy"
    assert info["name"] == "Dummy"
    assert info["status"] == "stopped"
    assert "capabilities" in info
    assert "metadata" in info
