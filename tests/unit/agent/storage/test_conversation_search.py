"""历史对话批量检索的窗口、证据和跨频道隔离。"""

from __future__ import annotations

import pytest

from agent.memory.memory_utils import pack_embedding


async def test_vector_search_keeps_scope_and_time_window_and_ignores_invalid_vectors(sqlite):
    rows = [
        ("qq:42", "最新地址南京", 1, pack_embedding([1., 0.])),
        ("qq:42", "旧地址北京", 2, pack_embedding([.8, .6])),
        ("telegram:42", "其他频道私事", 3, pack_embedding([1., 0.])),
        ("qq:42#other", "其他子会话", 4, pack_embedding([1., 0.])),
        ("qq:42", "损坏向量", 5, b"x"),
        ("qq:42", "非有限向量", 6, pack_embedding([float("nan"), 1.])),
        ("qq:42", "其他模型维度", 7, pack_embedding([1., 0., 0.])),
        ("qq:42", "窗口内最近消息", 8, pack_embedding([1., 0.])),
    ]
    for scope, content, ts, _ in rows:
        await sqlite.append_conversation(scope_type="user", scope_id=scope, role="user", content=content, ts_ns=ts)
    async with sqlite._write_tx() as db:
        await db.executemany("UPDATE conversation_messages SET embedding_blob=? WHERE content=?",
                             [(blob, content) for _, content, _, blob in rows])
        await db.commit()
    results = await sqlite.search_conversation_vector("user", "qq:42", [1., 0.], skip_recent=1, min_score=0)
    assert [r["content"] for r in results] == ["最新地址南京", "旧地址北京"]
    assert [r["score"] for r in results] == pytest.approx([1., .8])
    assert [r["ts_ns"] for r in results] == [1, 2]
    other = await sqlite.search_conversation_vector("user", "telegram:42", [1., 0.], skip_recent=0)
    assert [r["content"] for r in other] == ["其他频道私事"]
    await sqlite.close()
    assert [r["content"] for r in await sqlite.search_conversation_vector(
        "user", "qq:42", [1., 0.], skip_recent=1, min_score=0)] == ["最新地址南京", "旧地址北京"]
