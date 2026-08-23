"""一次性事件通知的历史固化语义（scheduler.enqueue_scope_reply）单元测试。

一次性事实（任务完成/推送/提醒到期）写目标会话的对话历史而非短期记忆：
不驻留 volatile 层（每轮催促已处理事项 + 反复打断缓存前缀），随窗口自然
滚动。历史写入失败回退短期记忆兜底（信息不丢）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agent.mind.tools.scheduler import _append_one_shot_history, enqueue_scope_reply


class _FakeRouter:
    def __init__(self, fail: bool = False) -> None:
        self.calls: List[tuple] = []
        self._fail = fail

    async def append(self, domain: Any, **kwargs: Any) -> None:
        if self._fail:
            raise RuntimeError("db down")
        self.calls.append((domain, kwargs))


def _pfc(router: _FakeRouter | None) -> SimpleNamespace:
    temporary: List[tuple] = []

    def add_temporary(clip: Dict, scope: str = "") -> None:
        temporary.append((clip, scope))

    conversation_data = SimpleNamespace(router=router) if router is not None else None
    return SimpleNamespace(
        add_temporary=add_temporary,
        temporary=temporary,
        pending_user=[],
        pending_group=[],
        previews={},
        adapter_keys={},
        conversation_data=conversation_data,
        set_message_preview=lambda s, p: None,
        set_adapter_key=lambda s, k: None,
    )


_SCOPE = "user_qq:1292917512"


@pytest.mark.asyncio
async def test_enqueue_writes_history_not_temporary() -> None:
    """一次性通知落对话历史（system / trigger_mind=False），不写短期记忆。"""
    router = _FakeRouter()
    pfc = _pfc(router)

    await enqueue_scope_reply(pfc, _SCOPE, "qq", "后台任务完成: 构建", "[后台任务完成] ...")

    assert len(router.calls) == 1
    domain, kwargs = router.calls[0]
    # scope 拆分对齐 crash_recovery 先例：parse_entity_scope 得 base 类型，
    # 余下整体（含 adapter 段）作 scope_id，与 Everything 落库口径一致
    assert kwargs["scope_type"] == "user"
    assert kwargs["scope_id"] == "qq:1292917512"
    assert kwargs["role"] == "system"
    assert kwargs["trigger_mind"] is False
    assert kwargs["adapter_key"] == "qq"
    assert kwargs["content"].startswith("[后台任务完成]")
    # 短期记忆零写入（不驻留 volatile 层）
    assert pfc.temporary == []
    # 入队与路由登记照旧
    assert pfc.pending_user == [_SCOPE]


@pytest.mark.asyncio
async def test_enqueue_group_scope_routes_to_group_queue() -> None:
    router = _FakeRouter()
    pfc = _pfc(router)
    await enqueue_scope_reply(pfc, "group_qq:g1", "", "推送", "[push:x] 内容")
    assert pfc.pending_group == ["group_qq:g1"]
    assert pfc.pending_user == []


@pytest.mark.asyncio
async def test_history_failure_falls_back_to_temporary() -> None:
    """router.append 失败 → 短期记忆兜底（信息不丢），入队照常。"""
    pfc = _pfc(_FakeRouter(fail=True))

    await enqueue_scope_reply(pfc, _SCOPE, "qq", "预览", "[后台任务完成] ...")

    assert len(pfc.temporary) == 1
    clip, scope = pfc.temporary[0]
    assert scope == _SCOPE
    assert clip["role"] == "system"
    assert pfc.pending_user == [_SCOPE]


@pytest.mark.asyncio
async def test_no_router_falls_back_to_temporary() -> None:
    """conversation_data / router 缺失（早期启动、测试替身）→ 兜底。"""
    pfc = _pfc(None)
    await enqueue_scope_reply(pfc, _SCOPE, "", "预览", "内容")
    assert len(pfc.temporary) == 1
    assert pfc.pending_user == [_SCOPE]


@pytest.mark.asyncio
async def test_invalid_scope_falls_back() -> None:
    """scope 无法解析（无下划线前缀）→ 兜底不炸。"""
    pfc = _pfc(_FakeRouter())
    await enqueue_scope_reply(pfc, "nonsense", "", "预览", "内容")
    assert len(pfc.temporary) == 1


@pytest.mark.asyncio
async def test_session_scope_roundtrip() -> None:
    """多会话 scope（user_webui:web_user#chat1）解析与写入正确。"""
    router = _FakeRouter()
    pfc = _pfc(router)
    await enqueue_scope_reply(
        pfc, "user_webui:web_user#chat1", "webui", "预览", "内容",
    )
    _domain, kwargs = router.calls[0]
    assert kwargs["scope_type"] == "user"
    assert kwargs["scope_id"] == "webui:web_user#chat1"


@pytest.mark.asyncio
async def test_append_direct_helper() -> None:
    """_append_one_shot_history 直达路径（委托轮内会合固化详情用）。"""
    router = _FakeRouter()
    pfc = _pfc(router)
    ok = await _append_one_shot_history(pfc, _SCOPE, "qq", "[后台委托完成] 详情")
    assert ok is True
    assert len(router.calls) == 1
    assert pfc.temporary == []
