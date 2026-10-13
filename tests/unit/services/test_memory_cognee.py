"""Cognee Web 配置保存不得先覆盖不兼容的数据卷配置。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.llm_bridge import CogneeConfigError
from services.memory import MemoryService


@pytest.mark.parametrize("compatible", [False, True])
async def test_save_validates_before_persisting_or_reconfiguring(monkeypatch, compatible):
    saved = Mock()
    validate = Mock(side_effect=None if compatible else CogneeConfigError("不兼容的向量模型"))
    coordinator = SimpleNamespace(
        client=SimpleNamespace(validate_configuration=validate), reconfigure=AsyncMock(),
    )
    monkeypatch.setattr("agent.memory.cognee.config.load_cognee_config", CogneeConfig)
    monkeypatch.setattr("agent.memory.cognee.config.save_cognee_config", saved)
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: coordinator)
    if compatible:
        result = await MemoryService.save_cognee_config({"chat": {"max_completion_tokens": 4096}})
        assert result["chat"]["max_completion_tokens"] == 4096
        saved.assert_called_once()
        coordinator.reconfigure.assert_awaited_once()
    else:
        with pytest.raises(CogneeConfigError, match="不兼容"):
            await MemoryService.save_cognee_config({"embedding": {"model": "different"}})
        saved.assert_not_called()
        coordinator.reconfigure.assert_not_awaited()
