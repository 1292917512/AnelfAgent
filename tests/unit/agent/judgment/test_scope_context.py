"""judge 工具的上下文注入测试：context_mode 三档（none/conversation/full）契约。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import agent.judgment.tools as tools
from agent.judgment.types import JudgmentReport, JudgmentSource, NoulAnswer


class _FakeSqlite:
    def __init__(
        self, rows: List[Dict[str, Any]], summary: Optional[Dict[str, Any]] = None
    ) -> None:
        self._rows = rows
        self._summary = summary
        self.fetch_calls: List[Dict[str, Any]] = []
        self.summary_calls: List[Dict[str, Any]] = []

    async def fetch_conversation(
        self, *, scope_type: str, scope_id: str, limit: int
    ) -> List[Dict[str, Any]]:
        self.fetch_calls.append({"scope_type": scope_type, "scope_id": scope_id, "limit": limit})
        return self._rows[-limit:]

    async def get_conversation_summary(
        self, *, scope_type: str, scope_id: str
    ) -> Optional[Dict[str, Any]]:
        self.summary_calls.append({"scope_type": scope_type, "scope_id": scope_id})
        return self._summary


def _fake_mind(sqlite: _FakeSqlite, *, max_size: int = 120) -> Any:
    return SimpleNamespace(
        conversation_data=SimpleNamespace(
            router=SimpleNamespace(sqlite=sqlite), max_size=max_size
        )
    )


class _FakeMindPort:
    def __init__(self, mind: Optional[Any]) -> None:
        self._mind = mind

    @property
    def bound(self) -> bool:
        return self._mind is not None

    def get(self) -> Any:
        assert self._mind is not None
        return self._mind


class _FakeEngine:
    def __init__(self) -> None:
        self.seen_state: Any = None

    async def judge(self, state: Any, questions: Dict[str, Any]) -> JudgmentReport:
        self.seen_state = state
        return JudgmentReport(
            answers={"q": NoulAnswer(noul=0.8)}, source=JudgmentSource.LLM_FALLBACK, model="m"
        )


def _patch_scope(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    from agent.mind.tool_activation import ToolActivationManager
    monkeypatch.setattr(ToolActivationManager, "current_scope", classmethod(lambda cls: scope))


def _patch_engine(monkeypatch: pytest.MonkeyPatch) -> _FakeEngine:
    engine = _FakeEngine()
    monkeypatch.setattr(tools, "get_judgment_engine", lambda: engine)
    return engine


def _patch_mind(monkeypatch: pytest.MonkeyPatch, sqlite: _FakeSqlite, max_size: int = 120) -> None:
    monkeypatch.setattr(
        "agent.mind.tools.ports.mind_port",
        _FakeMindPort(_fake_mind(sqlite, max_size=max_size)),
    )


_QUESTIONS = [{"id": "q", "type": "noul", "instructions": "用户在催促吗？"}]
_ROWS = [
    {"role": "user", "content": "我的订单一周没物流更新了"},
    {"role": "assistant", "content": "我帮你查"},
    {"role": "user", "content": "查到了吗"},
]


async def test_conversation_mode_injects_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _patch_engine(monkeypatch)
    _patch_scope(monkeypatch, "user_webui:web_user#chat_1")
    sqlite = _FakeSqlite(_ROWS)
    _patch_mind(monkeypatch, sqlite)

    result = json.loads(await tools.judge(questions=_QUESTIONS, context_mode="conversation"))

    assert result["ok"] and result["state_source"] == "auto_conversation"
    state = engine.seen_state
    assert "[user] 我的订单一周没物流更新了" in state
    assert state.endswith("[user] 查到了吗")
    # 子会话后缀贯通存储键
    assert sqlite.fetch_calls[0] == {
        "scope_type": "user", "scope_id": "webui:web_user#chat_1", "limit": 6,
    }
    assert sqlite.summary_calls == []  # conversation 档不读摘要


async def test_full_mode_injects_window_and_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _patch_engine(monkeypatch)
    _patch_scope(monkeypatch, "user_qq:123")
    sqlite = _FakeSqlite(_ROWS, summary={"summary": "用户此前已多次反馈物流问题"})
    _patch_mind(monkeypatch, sqlite, max_size=120)

    result = json.loads(await tools.judge(questions=_QUESTIONS, context_mode="full"))

    assert result["ok"] and result["state_source"] == "auto_full"
    state = engine.seen_state
    assert state.startswith("[对话摘要] 用户此前已多次反馈物流问题")
    assert "[user] 查到了吗" in state
    # full 档取数上限 = 会话窗口大小
    assert sqlite.fetch_calls[0]["limit"] == 120


async def test_default_none_without_state_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch)
    _patch_scope(monkeypatch, "user_qq:123")
    _patch_mind(monkeypatch, _FakeSqlite(_ROWS))

    result = json.loads(await tools.judge(questions=_QUESTIONS))

    assert result["cause"] == "param" and "context_mode" in result["hint"]


async def test_state_and_mode_conflict_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch)

    result = json.loads(await tools.judge(
        questions=_QUESTIONS, state="显式内容", context_mode="conversation",
    ))

    assert result["cause"] == "param" and "互斥" in result["error"]


async def test_invalid_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch)

    result = json.loads(await tools.judge(questions=_QUESTIONS, context_mode="everything"))

    assert result["cause"] == "param"
    assert "conversation" in result["hint"]


async def test_explicit_state_skips_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _patch_engine(monkeypatch)
    _patch_scope(monkeypatch, "user_qq:123")
    sqlite = _FakeSqlite(_ROWS)
    _patch_mind(monkeypatch, sqlite)

    result = json.loads(await tools.judge(questions=_QUESTIONS, state="显式传入的文档内容"))

    assert result["ok"] and result["state_source"] == "provided"
    assert engine.seen_state == "显式传入的文档内容"
    assert sqlite.fetch_calls == []


async def test_non_conversation_scope_param_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch)
    _patch_scope(monkeypatch, "reflect:abc123")
    _patch_mind(monkeypatch, _FakeSqlite(_ROWS))

    result = json.loads(await tools.judge(questions=_QUESTIONS, context_mode="conversation"))

    assert result["cause"] == "param" and "state" in result["hint"]


async def test_empty_history_param_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch)
    _patch_scope(monkeypatch, "user_qq:123")
    _patch_mind(monkeypatch, _FakeSqlite([]))

    result = json.loads(await tools.judge(questions=_QUESTIONS, context_mode="conversation"))
    assert result["cause"] == "param"


async def test_char_budget_keeps_newest(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _patch_engine(monkeypatch)
    _patch_scope(monkeypatch, "user_qq:123")
    sqlite = _FakeSqlite([
        {"role": "user", "content": "旧" * 300},
        {"role": "user", "content": "新消息"},
    ])
    _patch_mind(monkeypatch, sqlite)
    monkeypatch.setattr(
        tools, "get_config_int",
        lambda key, default=0: 200 if "max_chars" in key else 6,
    )

    result = json.loads(await tools.judge(questions=_QUESTIONS, context_mode="conversation"))

    assert result["ok"]
    state = engine.seen_state
    assert "新消息" in state and "旧" not in state
