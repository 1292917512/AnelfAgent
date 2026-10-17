"""agent/workflow 测试共享 fixture：journal 路径隔离 + 假 Mind 装配。"""

from __future__ import annotations

import pytest
from wf_helpers import FakeDelegationManager, make_stub_mind

from core.path import ConfigPaths


@pytest.fixture(autouse=True)
def _isolate_workflow_paths(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离工作流库与委托日志目录（ConfigPaths 解析真实目录，须显式重定向）。"""
    monkeypatch.setattr(ConfigPaths, "WORKFLOW_DB", str(tmp_path / "workflow.sqlite3"))
    monkeypatch.setattr(ConfigPaths, "DELEGATION_DIR", str(tmp_path / "delegations"))
    yield


@pytest.fixture()
def fake_manager():
    return FakeDelegationManager()


@pytest.fixture()
def stub_mind():
    return make_stub_mind()


@pytest.fixture()
async def engine(stub_mind, fake_manager):
    from agent.workflow.engine import WorkflowEngine
    eng = WorkflowEngine(stub_mind.background_tasks, fake_manager)
    yield eng
    await eng.aclose()
