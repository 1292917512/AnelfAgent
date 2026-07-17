from __future__ import annotations

from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.fusion import datasets_for_scope, reciprocal_rank_fusion
from agent.memory.memory_types import MemorySearchResult


def test_datasets_for_scope_isolated_and_hashed() -> None:
    config = CogneeConfig(dataset_prefix="test")

    first = datasets_for_scope(config, "user_sensitive-id", None)
    second = datasets_for_scope(config, "user_other-id", None)

    assert first[0] == "test_global"
    assert len(first) == 2
    assert "sensitive-id" not in first[1]
    assert first[1] != second[1]


def test_rrf_deduplicates_projected_native_memory() -> None:
    config = CogneeConfig(native_weight=1.0, cognee_weight=0.8)
    native = [
        MemorySearchResult(
            id="mem:7",
            snippet="The user prefers concise answers.",
            score=0.7,
            source="memory",
        ),
    ]
    projected = [
        MemorySearchResult(
            id="cognee:chunk",
            snippet=(
                "Memory type: semantic\nSource: test\nImportance: 0.7\n"
                "Tags: user:1\nMetadata: {}\n\n"
                "The user prefers concise answers."
            ),
            score=0.95,
            source="cognee_chunk",
        ),
    ]

    results = reciprocal_rank_fusion(native, projected, config=config, limit=5)

    assert len(results) == 1
    assert results[0].source == "memory"
    assert results[0].score == 1.0
