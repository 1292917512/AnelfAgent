from __future__ import annotations

import os
import uuid

import pytest

from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import CogneeConfig


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("COGNEE_INTEGRATION") != "1",
        reason="set COGNEE_INTEGRATION=1 with working model credentials",
    ),
]


@pytest.mark.asyncio
async def test_real_remember_recall_forget_cycle(tmp_path) -> None:
    dataset_name = f"anelf_test_{uuid.uuid4().hex[:10]}"
    client = CogneeClient(CogneeConfig(
        enabled=True,
        data_root=str(tmp_path / "cognee"),
        timeout_seconds=120,
    ))
    availability = await client.initialize()
    assert availability.ready, availability.reason

    try:
        await client.remember(
            "AnelfAgent integration marker is cobalt-orchid.",
            dataset_name=dataset_name,
            self_improvement=False,
        )
        results = await client.recall(
            "What is the integration marker?",
            datasets=[dataset_name],
            top_k=5,
        )
        assert any("cobalt-orchid" in item.content for item in results)
    finally:
        await client.forget(dataset=dataset_name)
