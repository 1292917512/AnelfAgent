"""未接收首条消息前也可读取已装配的运行时阶段。"""

from types import SimpleNamespace
from unittest.mock import patch

from agent.mind.autonomous import MindPhase
from agent.runtime.agent_app import AgentApp


def test_status_uses_registered_runtime_before_lazy_binding() -> None:
    app = AgentApp()
    runtime = SimpleNamespace(mind=SimpleNamespace(phase=MindPhase.IDLE))
    with patch("agent.runtime.singleton.get_runtime", return_value=runtime):
        assert app.get_status_info()["mind_phase"] == "idle"


def test_uninitialized_runtime_is_unknown() -> None:
    with patch("agent.runtime.singleton.get_runtime", return_value=None):
        assert AgentApp().get_status_info()["mind_phase"] == "unknown"
