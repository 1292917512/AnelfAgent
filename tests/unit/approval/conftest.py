"""权限测试的审计存储隔离。"""

import pytest


class _FakeAuditSink:
    """审批审计的内存假 sqlite（与 SqliteBackend 审计方法同接口）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._seq = 0

    async def append_approval_audit(self, record: dict) -> None:
        self._seq += 1
        self.rows.append({"id": self._seq, **record})

    async def approval_audit_stats(self) -> dict:
        by: dict[str, int] = {}
        for r in self.rows:
            by[r.get("outcome", "")] = by.get(r.get("outcome", ""), 0) + 1
        return {"total": len(self.rows), "by_outcome": by}

    async def list_approval_audit(self, limit: int = 50, offset: int = 0, tool_name: str = "") -> list[dict]:
        rows = [r for r in self.rows if not tool_name or r.get("tool_name") == tool_name]
        return list(reversed(rows))[offset:offset + limit]


@pytest.fixture()
def fake_audit_sink(monkeypatch: pytest.MonkeyPatch) -> _FakeAuditSink:
    """把审批审计数据面指向内存假 sqlite（隔离真实 DB）。"""
    import agent.approval.audit as audit_mod

    sink = _FakeAuditSink()
    monkeypatch.setattr(audit_mod, "_audit_sink", lambda: sink)
    return sink
