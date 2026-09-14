"""心跳日志写入审计：每次落盘打点调用方归因。

回归背景（2026-09-15 幽灵行事故）：heartbeat.md 出现无执行支撑的
「反思已登记/反思完成」行，写入时刻所有已知调用路径零痕迹，事后无法
反查写入者。审计行保证下次复发可从日志缓冲直接定位。
"""

from __future__ import annotations

from typing import List

import pytest

from agent.heartbeat import log as hb_log


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path) -> List[str]:
    captured: List[str] = []
    # LOG_PATH 指向不存在的临时文件：读写分支与真实环境解耦，测试互不残留
    monkeypatch.setattr(hb_log, "LOG_PATH", tmp_path / "heartbeat.md")
    monkeypatch.setattr(hb_log, "_atomic_write", lambda path, content: captured.append(content))
    monkeypatch.setattr(hb_log, "log", lambda msg, *args, **kwargs: captured.append(msg))
    return captured


class TestWriteAudit:
    def test_append_entry_audits_caller(self, _isolate: List[str]) -> None:
        """append_entry 落盘后打审计行：含内容前缀与模块外调用帧（本测试文件）。"""
        hb_log.append_entry("反思已登记，待空闲心跳执行: 对话质量下滑")
        audit_lines = [m for m in _isolate if m.startswith("心跳日志写入:")]
        assert len(audit_lines) == 1
        assert "test_log_audit.py" in audit_lines[0]
        assert "反思已登记" in audit_lines[0]

    def test_write_log_audits_entry(self, _isolate: List[str]) -> None:
        """write_log 落盘后打审计行：含任务名摘要。"""
        hb_log.write_log(task_names=["reply"], exec_results=["reply 成功"])
        audit_lines = [m for m in _isolate if m.startswith("心跳日志写入:")]
        assert len(audit_lines) == 1
        assert "reply" in audit_lines[0]
