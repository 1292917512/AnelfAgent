"""操作实体（entities/operation）单元测试：目录持久化 / 执行降级 / MCP 网关 / 历史。"""

from __future__ import annotations

import json

import pytest

from entities.operation import desktop, executor, framework


@pytest.fixture
def operations_file(tmp_path, monkeypatch):
    target = tmp_path / "operations.json"
    monkeypatch.setattr(framework, "_store_path", lambda: target)
    return target


class TestCatalog:
    def test_builtin_desktop_actions_listed(self, operations_file):
        specs = framework.list_operations()
        ids = [s.id for s in specs]
        assert "desktop.click" in ids and "desktop.type" in ids
        assert all(not s.removable for s in specs if s.kind == "desktop")

    def test_annotation_and_disable_persist(self, operations_file):
        assert framework.set_annotation("desktop.click", "常用左键点击")
        assert framework.set_enabled("desktop.hotkey", False)
        data = json.loads(operations_file.read_text("utf-8"))
        assert data["annotations"]["desktop.click"] == "常用左键点击"
        assert "desktop.hotkey" in data["disabled"]
        spec = framework.get_operation("desktop.click")
        assert spec.annotation == "常用左键点击"
        assert framework.get_operation("desktop.hotkey").enabled is False

    def test_annotation_clears_with_empty(self, operations_file):
        framework.set_annotation("desktop.click", "有")
        assert framework.set_annotation("desktop.click", "")
        assert framework.get_operation("desktop.click").annotation == ""

    def test_annotation_rejects_unknown_op(self, operations_file):
        assert framework.set_annotation("desktop.nope", "x") is False

    def test_mcp_register_override_and_remove(self, operations_file):
        spec = framework.register_mcp_operation(
            server="playwright", tool="playwright__browser_navigate",
            annotation="打开网页", description="Navigate to a URL",
            params=[{"name": "url", "type": "string", "required": True}],
        )
        assert spec.id == "mcp.playwright.browser_navigate"
        # 同 id 覆盖更新
        framework.register_mcp_operation(
            server="playwright", tool="playwright__browser_navigate",
            annotation="导航到网址",
        )
        assert framework.get_operation(spec.id).annotation == "导航到网址"
        assert len([s for s in framework.list_operations() if s.id == spec.id]) == 1
        assert framework.remove_operation(spec.id) is True
        assert framework.get_operation(spec.id) is None

    def test_builtin_not_removable(self, operations_file):
        assert framework.remove_operation("desktop.click") is False


class TestExecutor:
    async def test_disabled_operation_rejected(self, operations_file):
        framework.set_enabled("desktop.click", False)
        outcome = await executor.execute("desktop.click", {"x": 1, "y": 2})
        assert outcome["ok"] is False and "停用" in outcome["error"]

    async def test_unknown_operation(self, operations_file):
        outcome = await executor.execute("mcp.no.nope", {})
        assert outcome["ok"] is False

    @pytest.mark.skipif(desktop.runtime_ready(), reason="本机装有 pyautogui")
    async def test_desktop_missing_runtime_hint(self, operations_file):
        outcome = await executor.execute("desktop.click", {"x": 1, "y": 2})
        assert outcome["ok"] is False and "install_python_packages" in outcome["error"]

    async def test_mcp_via_gateway_and_history(self, operations_file, monkeypatch):
        framework.register_mcp_operation(
            server="srv", tool="srv__do_thing", annotation="做事",
        )
        calls: list = []

        async def _call(tool: str, args: dict) -> str:
            calls.append((tool, args))
            return json.dumps({"success": True, "result": "干完了"})

        gateway = executor.McpGateway(
            call=_call,
            connected_servers=lambda: {"srv": ["srv__do_thing"]},
            server_status=lambda: [{"name": "srv", "connected": True, "tool_count": 1}],
        )
        monkeypatch.setattr(executor, "mcp_gateway", lambda: gateway)

        outcome = await executor.execute("mcp.srv.do_thing", {"k": 1})
        assert outcome["ok"] is True and outcome["result"] == "干完了"
        assert calls == [("srv__do_thing", {"k": 1})]
        entry = executor.history(1)[0]
        assert entry["op"] == "mcp.srv.do_thing" and entry["ok"] is True
        assert executor.seconds_since_activity() < 5  # 执行触发活跃窗口

    async def test_mcp_error_result_maps_to_failure(self, operations_file, monkeypatch):
        framework.register_mcp_operation(server="srv", tool="srv__bad")

        async def _call(tool: str, args: dict) -> str:
            return json.dumps({"success": False, "error": "boom"})

        gateway = executor.McpGateway(
            call=_call, connected_servers=lambda: {}, server_status=lambda: [],
        )
        monkeypatch.setattr(executor, "mcp_gateway", lambda: gateway)
        outcome = await executor.execute("mcp.srv.bad", {})
        assert outcome["ok"] is False and "boom" in outcome["error"]

    async def test_mcp_gateway_absent(self, operations_file):
        framework.register_mcp_operation(server="srv", tool="srv__x")
        outcome = await executor.execute("mcp.srv.x", {})
        assert outcome["ok"] is False and "网关" in outcome["error"]


class TestContextProvider:
    async def test_idle_window_zero_injection(self, operations_file, monkeypatch):
        """静默期（活跃窗口外）零注入——操作语境才触发态势。"""
        from entities.operation.context import OperationProvider

        monkeypatch.setattr(executor, "seconds_since_activity", lambda: float("inf"))
        assert await OperationProvider().provide("scope") is None

    async def test_active_window_renders_linked_ops(self, operations_file, monkeypatch):
        from entities.operation.context import OperationProvider

        framework.register_mcp_operation(
            server="playwright", tool="playwright__browser_navigate",
            annotation="打开网页",
        )
        monkeypatch.setattr(executor, "seconds_since_activity", lambda: 10.0)
        snap = await OperationProvider().provide("user_webui:u1")
        assert snap is not None
        text = snap.content or ""
        assert "mcp.playwright.browser_navigate" in text
        assert "打开网页" in text
        assert "mcp:playwright" in text  # 关联指向执行工具组
        assert "桌面操控" in text  # 纪律行（就绪时）或不可用提示

    async def test_unregistered_servers_not_listed(self, operations_file, monkeypatch):
        """未关联 server 的工具清单不进注入（只列关联，不列目录）。"""
        from entities.operation.context import OperationProvider

        gateway = executor.McpGateway(
            call=None, connected_servers=lambda: {"huge": [f"t{i}" for i in range(194)]},
            server_status=lambda: [],
        )
        monkeypatch.setattr(executor, "mcp_gateway", lambda: gateway)
        monkeypatch.setattr(executor, "seconds_since_activity", lambda: 10.0)
        snap = await OperationProvider().provide("scope")
        text = (snap.content if snap else "") or ""
        assert "huge" not in text
