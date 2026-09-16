"""决策目标规范化解析回归测试（2026-09-16 影子会话事故）。

事故链：元决策 decide 的 target 是 LLM 自由文本——旧格式 user_123 经
require_pending=False 分支拼出裸 id 会话（真实待回复条目不被消费，随后
重复触发补提轮）；qq:123 / qq_123 经主动消息兜底整串塞进 uid 拼出双前缀
会话。内部触发轮的 [已执行操作摘要] 因此落进影子 scope（user:qq:qq:1292917512
等 7 条实证，09-14 起错位）。

收敛：决策目标一律先规范化（normalize_target_scope）——无法唯一解析的
回退队列消费（REPLY）或放弃（PROACTIVE）；队列内不可路由条目就地清除。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent.messages.presets import MessageAssistant, MessageAssistantGroup
from agent.mind.tools.decision_executor import (
    normalize_target_scope,
    pop_next_reply_target,
    resolve_reply_target,
    routable_target,
)
from agent.mind.work_memory import WorkMemory

_UID = 1292917512
_CANONICAL = "user_qq:1292917512"


def _mind(*, queued: tuple[str, ...] = (), registered: tuple[tuple[str, str], ...] = ()) -> Any:
    pfc = WorkMemory(SimpleNamespace())  # type: ignore[arg-type]
    for scope in queued:
        (pfc.pending_group if scope.startswith("group_") else pfc.pending_user).append(scope)
    for scope, adapter in registered:
        pfc.set_adapter_key(scope, adapter)
    return SimpleNamespace(pfc=pfc, _active_scopes=set())


class TestNormalizeTargetScope:
    def test_canonical_scope_passthrough(self) -> None:
        mind = _mind()
        assert normalize_target_scope(mind, "user_qq:123") == "user_qq:123"
        assert normalize_target_scope(mind, "group_qq:456") == "group_qq:456"
        assert normalize_target_scope(mind, "user_webui:u#chat1") == "user_webui:u#chat1"

    def test_resolution_via_pfc_facade(self) -> None:
        """解析经 PrefrontalCortex 门面可达（运行时 mind.pfc 是门面而非 WorkMemory）。"""
        from unittest.mock import AsyncMock

        from agent.mind.prefrontal_cortex import PrefrontalCortex

        pfc = PrefrontalCortex(
            everything_data=SimpleNamespace(get_anything=AsyncMock(return_value=None)),
        )
        pfc.pending_user.append(_CANONICAL)
        mind = SimpleNamespace(pfc=pfc, _active_scopes=set())
        assert normalize_target_scope(mind, f"user_{_UID}") == _CANONICAL

    def test_legacy_prefix_resolved_from_known_scopes(self) -> None:
        """旧格式 user_123 按已知会话补频道前缀，不再产出裸 id scope。"""
        mind = _mind(queued=(_CANONICAL,))
        assert normalize_target_scope(mind, f"user_{_UID}") == _CANONICAL
        mind = _mind(queued=("group_qq:1104224649",))
        assert normalize_target_scope(mind, "group_1104224649") == "group_qq:1104224649"

    def test_bare_id_resolved_from_known_scopes(self) -> None:
        mind = _mind(queued=(_CANONICAL,))
        assert normalize_target_scope(mind, str(_UID)) == _CANONICAL

    def test_channel_prefix_forms_resolved(self) -> None:
        """qq:123 与下划线变体 qq_123 均命中已知会话，不拼双前缀。"""
        mind = _mind(queued=(_CANONICAL,))
        assert normalize_target_scope(mind, f"qq:{_UID}") == _CANONICAL
        assert normalize_target_scope(mind, f"qq_{_UID}") == _CANONICAL

    def test_registry_only_scope_resolves(self) -> None:
        """无待回复条目但路由登记在案（主动消息场景）同样可解析。"""
        mind = _mind(registered=((_CANONICAL, "qq"),))
        assert normalize_target_scope(mind, str(_UID)) == _CANONICAL

    def test_ambiguous_bare_id_refused(self) -> None:
        """同 base id 命中多个会话（user/group 同号）时拒绝猜测。"""
        mind = _mind(queued=("user_qq:7", "group_qq:7"))
        assert normalize_target_scope(mind, "7") == ""

    def test_unknown_target_refused(self) -> None:
        mind = _mind(queued=(_CANONICAL,))
        for bad in ("hello", "404", "user_404", "", "  "):
            assert normalize_target_scope(mind, bad) == ""


class TestResolveReplyTarget:
    async def test_legacy_target_consumes_canonical_entry(self) -> None:
        """旧格式目标消费的是规范化后的真实条目——不再残留补提轮。"""
        mind = _mind(queued=(_CANONICAL,))
        msg = resolve_reply_target(mind, f"user_{_UID}")
        assert isinstance(msg, MessageAssistant)
        assert msg.uid == _UID
        assert msg.adapter_key == "qq"
        assert msg.entity_scope == _CANONICAL
        assert mind.pfc.peek_all_tasks() == []

    async def test_channel_prefix_target_consumes_entry(self) -> None:
        mind = _mind(queued=(_CANONICAL,))
        msg = resolve_reply_target(mind, f"qq:{_UID}")
        assert msg is not None
        assert msg.adapter_key == "qq"
        assert mind.pfc.peek_all_tasks() == []

    async def test_unresolvable_target_keeps_queue(self) -> None:
        """无法解析的目标不动队列，由调用方回退顺序消费。"""
        mind = _mind(queued=(_CANONICAL,))
        assert resolve_reply_target(mind, "nonsense") is None
        assert mind.pfc.peek_all_tasks() != []

    async def test_active_scope_not_consumed(self) -> None:
        mind = _mind(queued=(_CANONICAL,))
        mind._active_scopes.add(_CANONICAL)
        assert resolve_reply_target(mind, _CANONICAL) is None
        assert mind.pfc.peek_all_tasks() != []


class TestRoutableTarget:
    def test_canonical_target_without_queue(self) -> None:
        """主动消息到显式规范 scope 不要求待回复事实。"""
        mind = _mind()
        msg = routable_target(mind, "user_qq:123")
        assert isinstance(msg, MessageAssistant)
        assert msg.uid == 123
        assert msg.adapter_key == "qq"

    def test_group_target(self) -> None:
        mind = _mind()
        msg = routable_target(mind, "group_qq:456")
        assert isinstance(msg, MessageAssistantGroup)
        assert msg.group_id == 456
        assert msg.adapter_key == "qq"

    def test_known_scope_by_bare_id(self) -> None:
        mind = _mind(registered=((_CANONICAL, "qq"),))
        msg = routable_target(mind, str(_UID))
        assert msg is not None
        assert msg.entity_scope == _CANONICAL

    def test_unknown_target_refused_no_shadow(self) -> None:
        """无法唯一解析的目标直接放弃——不再产出 qq:qq: / qq_ 影子会话。"""
        mind = _mind()
        for bad in (f"qq:{_UID}", f"qq_{_UID}", str(_UID), "proactive"):
            assert routable_target(mind, bad) is None


class TestPopNextPurgesUnroutable:
    async def test_adapterless_entry_purged_valid_returned(self) -> None:
        """缺频道前缀的队列条目就地清除，后面的规范条目正常返回。"""
        mind = _mind(queued=("user_1", "user_qq:2"))
        target = await pop_next_reply_target(mind)
        assert target is not None
        assert target.uid == 2
        assert target.adapter_key == "qq"
        assert mind.pfc.has_pending_tasks() is False

    async def test_all_unroutable_leaves_queue_empty(self) -> None:
        mind = _mind(queued=("user_1", "2", "_global"))
        assert await pop_next_reply_target(mind) is None
        assert mind.pfc.has_pending_tasks() is False
