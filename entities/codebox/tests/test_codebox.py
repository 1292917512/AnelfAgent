"""代码编排（entities/codebox）测试：工具桥单测 + 子进程端到端。

端到端用真实子进程跑脚本（注册表挂测试专用工具，审批门换自动放行替身），
覆盖：循环/条件编排、失败捕获续跑、排除清单、未知工具、上限、截断、超时强杀。
"""

import json
import time

import pytest

from core.entity import EntityRegistry, ToolParam
from entities.codebox import sandbox

_TEST_TOOLS_REGISTERED = False


def _ensure_test_tools() -> None:
    """注册测试专用工具（幂等；名称带 codebox_test_ 前缀防碰撞）。"""
    global _TEST_TOOLS_REGISTERED
    if _TEST_TOOLS_REGISTERED:
        return

    async def _echo(text: str = "") -> str:
        return f"echo:{text}"

    async def _fail() -> str:
        raise ValueError("故意失败")

    async def _big() -> str:
        return "x" * 30000

    EntityRegistry.register_tool(
        "codebox_test_echo", _echo, "测试回显", group="codebox_test",
        params=[ToolParam(name="text", description="文本", type="string", required=False)])
    EntityRegistry.register_tool(
        "codebox_test_fail", _fail, "测试故意失败", group="codebox_test")
    EntityRegistry.register_tool(
        "codebox_test_big", _big, "测试超长输出", group="codebox_test")
    _TEST_TOOLS_REGISTERED = True


@pytest.fixture()
def gate(monkeypatch):
    """审批门替身（默认自动放行；calls 记录每次审批请求）。"""
    from agent.approval.gate import ApprovalDecision

    class _FakeGate:
        def __init__(self) -> None:
            self.decision = ApprovalDecision.APPROVED
            self.calls: list = []

        async def request_approval(self, **kwargs):
            self.calls.append(kwargs)
            return self.decision

    fake = _FakeGate()
    monkeypatch.setattr("agent.approval.gate.get_approval_gate", lambda: fake)
    return fake


@pytest.fixture()
def ws(tmp_path) -> str:
    """脚本工作目录隔离到 tmp（不碰真实 workspace）。"""
    return str(tmp_path)


# ------------------------------------------------------------------
# 工具桥（无子进程）
# ------------------------------------------------------------------

class TestDispatch:
    async def test_excluded_tool_rejected(self, gate) -> None:
        _ensure_test_tools()
        result = await sandbox._dispatch("send_message", {"text": "x"}, 8000)
        assert result["ok"] is False
        assert "不可用" in result["error"]
        assert gate.calls == []  # 排除清单在审批门之前

    async def test_approval_denied_blocks_execution(self, gate) -> None:
        from agent.approval.gate import ApprovalDecision
        _ensure_test_tools()
        gate.decision = ApprovalDecision.DENIED
        result = await sandbox._dispatch("codebox_test_echo", {"text": "hi"}, 8000)
        assert result["ok"] is False
        assert "审批未通过" in result["error"]
        assert gate.calls[0]["tool_name"] == "codebox_test_echo"
        assert gate.calls[0]["channel"] is None

    async def test_approval_gate_failure_fails_open(self, gate, monkeypatch) -> None:
        _ensure_test_tools()

        async def _boom(**kwargs):
            raise RuntimeError("gate down")

        gate.request_approval = _boom
        result = await sandbox._dispatch("codebox_test_echo", {"text": "hi"}, 8000)
        assert result["ok"] is True and result["result"] == "echo:hi"

    async def test_tool_error_json_is_failure(self, gate) -> None:
        _ensure_test_tools()
        result = await sandbox._dispatch("codebox_test_fail", {}, 8000)
        assert result["ok"] is False
        assert "故意失败" in result["error"]
        assert result["ledger"]["ok"] is False

    async def test_result_truncated_head_tail(self, gate) -> None:
        _ensure_test_tools()
        result = await sandbox._dispatch("codebox_test_big", {}, 8000)
        assert result["ok"] is True
        assert len(result["result"]) < 9000
        assert "已截断" in result["result"]

    async def test_catalog_lists_callable_excludes_output(self, gate) -> None:
        _ensure_test_tools()
        result = await sandbox._dispatch("__list__", {}, 8000)
        assert result["ok"] is True
        catalog = json.loads(result["result"])
        names = [e["name"] for e in catalog]
        assert "codebox_test_echo" in names
        assert "send_message" not in names
        assert "run_python" not in names


# ------------------------------------------------------------------
# 子进程端到端
# ------------------------------------------------------------------

class TestRunScript:
    async def test_loop_and_conditional(self, gate, ws) -> None:
        _ensure_test_tools()
        result = await sandbox.run_script(
            "total = 0\n"
            "for i in range(5):\n"
            "    r = tools.codebox_test_echo(text=str(i))\n"
            "    total += int(r.split(':')[1])\n"
            "print('sum =', total)\n",
            workspace_root=ws)
        assert result.startswith("[代码编排] 完成：5 次工具调用")
        assert "sum = 10" in result

    async def test_tool_error_caught_and_continue(self, gate, ws) -> None:
        _ensure_test_tools()
        result = await sandbox.run_script(
            "try:\n"
            "    tools.codebox_test_fail()\n"
            "except ToolError as e:\n"
            "    print('caught:', '故意失败' in str(e))\n"
            "print('continued')\n",
            workspace_root=ws)
        assert "caught: True" in result
        assert "continued" in result
        assert "1 次失败" in result

    async def test_excluded_tool_message(self, gate, ws) -> None:
        _ensure_test_tools()
        result = await sandbox.run_script(
            "try:\n"
            "    tools.send_message(text='你好')\n"
            "except ToolError as e:\n"
            "    print('blocked:', '脚本外' in str(e))\n",
            workspace_root=ws)
        assert "blocked: True" in result

    async def test_unknown_tool_carries_registry_hint(self, gate, ws) -> None:
        _ensure_test_tools()
        result = await sandbox.run_script(
            "try:\n"
            "    tools.codebox_test_ech(text='x')\n"
            "except ToolError as e:\n"
            "    print('has suggestion:', 'codebox_test_echo' in str(e))\n",
            workspace_root=ws)
        assert "has suggestion: True" in result

    async def test_tools_list(self, gate, ws) -> None:
        _ensure_test_tools()
        result = await sandbox.run_script(
            "catalog = tools.list()\n"
            "print('echo' in catalog, 'send_message' in catalog)\n",
            workspace_root=ws)
        assert "True False" in result

    async def test_call_cap(self, gate, ws) -> None:
        from core.config import ConfigManager
        ConfigManager.set("codebox_max_tool_calls", 3)
        _ensure_test_tools()
        result = await sandbox.run_script(
            "for i in range(5):\n"
            "    try:\n"
            "        tools.codebox_test_echo(text=str(i))\n"
            "    except ToolError as e:\n"
            "        print('cap at', i, '上限' in str(e))\n"
            "        break\n",
            workspace_root=ws)
        assert "cap at 3 True" in result

    async def test_output_cap(self, gate, ws) -> None:
        from core.config import ConfigManager
        ConfigManager.set("codebox_output_chars", 200)
        _ensure_test_tools()
        result = await sandbox.run_script(
            "for i in range(50):\n"
            "    print('line', i, 'x' * 30)\n",
            workspace_root=ws)
        assert "已截断" in result

    async def test_timeout_kills_runaway(self, gate, ws) -> None:
        _ensure_test_tools()
        started = time.monotonic()
        result = await sandbox.run_script("while True:\n    pass\n",
                                          timeout=1, workspace_root=ws)
        elapsed = time.monotonic() - started
        payload = json.loads(result)
        assert payload["cause"] == "timeout"
        assert elapsed < 15

    async def test_script_exception_returns_traceback(self, gate, ws) -> None:
        _ensure_test_tools()
        result = await sandbox.run_script("raise RuntimeError('自定义崩溃')\n",
                                          workspace_root=ws)
        payload = json.loads(result)
        assert "自定义崩溃" in payload["error"]
        assert "Traceback" in payload["output_so_far"]

    async def test_empty_script_rejected(self, gate, ws) -> None:
        result = await sandbox.run_script("   \n", workspace_root=ws)
        payload = json.loads(result)
        assert payload["cause"] == "param"
