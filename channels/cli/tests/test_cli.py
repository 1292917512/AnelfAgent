"""CLI 频道配置与终端发送契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock

from agent.channel.config import load_channel_config_model
from agent.channel.schemas import AdapterChannel, SendRequest, SendSegment
from channels.cli.adapter import CLIChannel, run_cli
from channels.cli.config import CLIConfig


def test_cli_config_uses_standard_model() -> None:
    assert load_channel_config_model("cli") is CLIConfig
    assert CLIConfig().enabled is False


async def test_cli_rejects_unrendered_segments(capsys) -> None:
    channel = CLIChannel()
    response = await channel.forward_message(SendRequest(
        adapter_key="cli",
        channel=AdapterChannel(channel_id="cli_user"),
        segments=[
            SendSegment(type="text", content="你好"),
            SendSegment(type="image", file_path="photo.png"),
        ],
    ))
    assert response.success is False
    assert "image" in (response.error or "")
    assert capsys.readouterr().out == ""


async def test_cli_rejects_other_target(capsys) -> None:
    channel = CLIChannel()
    response = await channel.forward_message(SendRequest(
        adapter_key="cli",
        channel=AdapterChannel(channel_id="someone_else"),
        segments=[SendSegment(type="text", content="你好")],
    ))
    assert response.success is False
    assert "目标" in (response.error or "")
    assert capsys.readouterr().out == ""


async def test_cli_input_uses_registered_channel(monkeypatch) -> None:
    from agent import channel as channel_module
    from agent.runtime import agent_app as app_module

    channel = CLIChannel()
    start_channel = AsyncMock(return_value=True)
    manager = type("Manager", (), {
        "get": lambda self, channel_id: channel,
        "start_channel": start_channel,
    })()
    send_message = AsyncMock()
    app = type("App", (), {"send_message": send_message})()
    entries = iter(["你好", "quit"])
    monkeypatch.setattr(channel_module, "get_channel_manager", lambda: manager)
    monkeypatch.setattr(app_module, "get_agent_app", lambda: app)
    monkeypatch.setattr("builtins.input", lambda prompt: next(entries))

    await run_cli()

    start_channel.assert_awaited_once_with("cli")
    send_message.assert_awaited_once_with(
        user_id="cli_user", content="你好", user_name="用户",
        to_me=True, adapter_key="cli",
    )
