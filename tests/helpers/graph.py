"""构造已经明确核验的关系，供事实召回和派生索引测试使用。"""
from typing import Any

from agent.memory.graph.store import GraphStore


async def verify_relation(graph: GraphStore, edge: dict[str, Any]) -> dict[str, Any]:
    current = await graph.get_relation(edge["id"])
    assert current is not None
    if current["verification"] == "verified":
        return current
    verified = await graph.update_relation(
        edge["id"], evidence=current["evidence"] or "测试中的明确来源",
        verification="verified", verification_reason="已核对测试提供的原始证据",
        expected_revision=current["revision"],
    )
    assert verified is not None
    return verified


async def add_verified_relation(graph: GraphStore, *args: str, **kwargs: Any) -> dict[str, Any]:
    """事实消费测试的输入：创建关系并按当前修订核验，避免把候选当成事实。"""
    return await verify_relation(graph, await graph.add_relation(*args, **kwargs))
