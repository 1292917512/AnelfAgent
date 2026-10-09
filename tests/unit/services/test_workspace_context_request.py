from unittest.mock import AsyncMock, patch

import pytest

from services.chat import ChatService
from services.workspace_context import WorkspaceContext


@pytest.mark.asyncio
async def test_message_uses_its_own_workspace_snapshot() -> None:
    service = ChatService()
    own = WorkspaceContext(active_file="own.txt")
    with patch.object(service, "send_message", new_callable=AsyncMock) as send:
        await service.send_web_message("check", workspace_context=own)
        assert "own.txt" in send.call_args.args[0]
        await service.send_web_message("without context")
        assert send.call_args.args[0] == "without context"
