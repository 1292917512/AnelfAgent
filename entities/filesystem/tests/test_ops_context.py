"""文件操作态势（ops_context）测试：记录 / 渲染 / TTL / 目录说明文档。"""

from __future__ import annotations

import time

import pytest

import entities._sdk as sdk
from entities._sdk import ToolOp
from entities.filesystem import ops_context, shell_state

_SCOPE = "user_test:1"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """隔离态势状态并把工作区指到临时目录。"""
    monkeypatch.setattr(ops_context, "_workspace_root", lambda: str(tmp_path))
    ops_context._sessions.clear()
    ops_context._doc_cache.clear()
    shell_state._cwds.pop(_SCOPE, None)
    yield tmp_path
    ops_context._sessions.clear()
    ops_context._doc_cache.clear()
    shell_state._cwds.pop(_SCOPE, None)


def _op(tool: str = "read_file", target: str = "a.py", ok: bool = True,
        note: str = "", scope: str = _SCOPE) -> ToolOp:
    return ToolOp(
        scope=scope, tool=tool, target=target,
        targets=(target,) if target else (), arguments={}, ok=ok,
        note=note, duration_ms=3,
    )


class TestRecordAndRender:
    def test_no_session_renders_none(self, workspace) -> None:
        assert ops_context.render_session(_SCOPE) is None

    def test_render_contains_cwd_and_ops(self, workspace) -> None:
        ops_context.record_tool_op(_op(tool="read_file", target="sub/a.py"))
        ops_context.record_tool_op(_op(tool="edit_file", target="sub/a.py",
                                       ok=False, note="未在文件中找到要替换的字符串"))
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        assert "[文件操作态势]" in text
        assert "当前 Shell 目录:" in text
        assert "read_file sub/a.py ✓" in text
        assert "edit_file sub/a.py ✗（未在文件中找到要替换的字符串）" in text

    def test_scope_isolation(self, workspace) -> None:
        ops_context.record_tool_op(_op(scope="user_a:1"))
        assert ops_context.render_session("user_b:2") is None

    def test_global_scope_not_recorded(self, workspace) -> None:
        ops_context.record_tool_op(_op(scope="_global"))
        assert "_global" not in ops_context._sessions

    def test_ops_trimmed_to_max(self, workspace, monkeypatch) -> None:
        monkeypatch.setattr(
            ops_context, "get_config_int",
            lambda key, default=0: 3 if key == "os_context_max_ops" else default,
        )
        for i in range(6):
            ops_context.record_tool_op(_op(target=f"f{i}.py"))
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        assert "f5.py" in text and "f2.py" not in text

    def test_ttl_expiry_drops_session(self, workspace, monkeypatch) -> None:
        ops_context.record_tool_op(_op())
        ops_context._sessions[_SCOPE].last_active = time.time() - 700
        assert ops_context.render_session(_SCOPE) is None
        assert _SCOPE not in ops_context._sessions


class TestDocs:
    def test_agents_md_injected_for_active_dir(self, workspace) -> None:
        sub = workspace / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("# 项目约定\n请遵守。", encoding="utf-8")
        ops_context.record_tool_op(_op(target="sub/a.py"))
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        assert "目录说明文档:" in text
        assert "── " + str(sub / "AGENTS.md") + " ──" in text
        assert "# 项目约定" in text

    def test_doc_names_config_order_and_basename_only(self, workspace, monkeypatch) -> None:
        (workspace / "README.md").write_text("readme", encoding="utf-8")
        (workspace / "GUIDE.md").write_text("guide", encoding="utf-8")
        monkeypatch.setattr(
            ops_context, "get_config",
            lambda key, default=None: "GUIDE.md,../secret,README.md"
            if key == "os_context_doc_names" else default,
        )
        ops_context.record_tool_op(_op(target="a.py"))
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        # 每个目录取配置序首个命中；含路径分隔符的条目被拒绝
        assert "guide" in text
        assert "readme" not in text
        assert "secret" not in text

    def test_docs_disabled_by_config(self, workspace, monkeypatch) -> None:
        (workspace / "AGENTS.md").write_text("doc", encoding="utf-8")
        monkeypatch.setattr(
            ops_context, "get_config_bool",
            lambda key, default=False: False if key == "os_context_docs_enabled" else default,
        )
        ops_context.record_tool_op(_op(target="a.py"))
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        assert "目录说明文档" not in text

    def test_doc_refresh_on_mtime_change(self, workspace) -> None:
        doc = workspace / "AGENTS.md"
        doc.write_text("v1", encoding="utf-8")
        assert ops_context._read_doc(str(doc), 3000) == "v1"
        import os
        os.utime(doc, (time.time() + 5, time.time() + 5))
        doc.write_text("v2", encoding="utf-8")
        assert ops_context._read_doc(str(doc), 3000) == "v2"

    def test_doc_truncated(self, workspace) -> None:
        doc = workspace / "AGENTS.md"
        doc.write_text("x" * 5000, encoding="utf-8")
        content = ops_context._read_doc(str(doc), 300)
        assert content is not None
        assert len(content) < 500
        assert "已截断" in content


class TestTargetDir:
    def test_file_target_yields_parent(self, workspace) -> None:
        sub = workspace / "sub"
        sub.mkdir()
        assert ops_context._target_dir("sub/a.py", str(workspace)) == str(sub)

    def test_dir_target_yields_itself(self, workspace) -> None:
        sub = workspace / "sub"
        sub.mkdir()
        assert ops_context._target_dir("sub", str(workspace)) == str(sub)

    def test_shell_command_not_a_path(self, workspace) -> None:
        assert ops_context._target_dir("git status", str(workspace)) is None
        assert ops_context._target_dir("rm -rf /tmp/x", str(workspace)) is None


class TestTrackOpsDecorator:
    def test_sync_tool_reported(self, workspace, monkeypatch) -> None:
        monkeypatch.setattr(sdk, "get_current_scope", lambda: _SCOPE)

        @sdk.track_ops(ops_context.record_tool_op, "file_path")
        def fake_tool(file_path: str) -> str:
            return '{"ok": true}'

        assert fake_tool("x/a.py") == '{"ok": true}'
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        assert "fake_tool x/a.py ✓" in text

    def test_error_json_marked_failed(self, workspace, monkeypatch) -> None:
        monkeypatch.setattr(sdk, "get_current_scope", lambda: _SCOPE)

        @sdk.track_ops(ops_context.record_tool_op, "file_path")
        def fake_tool(file_path: str) -> str:
            return '{"error": "文件不存在", "cause": "not_found"}'

        fake_tool("x/a.py")
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        assert "fake_tool x/a.py ✗（文件不存在）" in text

    def test_exception_reported_and_reraised(self, workspace, monkeypatch) -> None:
        monkeypatch.setattr(sdk, "get_current_scope", lambda: _SCOPE)

        @sdk.track_ops(ops_context.record_tool_op, "file_path")
        def fake_tool(file_path: str) -> str:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            fake_tool("x/a.py")
        text = ops_context.render_session(_SCOPE)
        assert text is not None
        assert "✗（RuntimeError: boom）" in text

    async def test_async_tool_stays_coroutine(self, workspace, monkeypatch) -> None:
        import inspect

        monkeypatch.setattr(sdk, "get_current_scope", lambda: _SCOPE)

        @sdk.track_ops(ops_context.record_tool_op, "name")
        async def fake_async(name: str = "") -> str:
            return '{"ok": true}'

        assert inspect.iscoroutinefunction(fake_async)
        await fake_async("web")
        assert ops_context.render_session(_SCOPE) is not None

    def test_signature_preserved_for_param_extraction(self) -> None:
        import inspect

        @sdk.track_ops(lambda op: None, "file_path")
        def fake_tool(file_path: str, offset: int = 0) -> str:
            return ""

        sig = inspect.signature(fake_tool)
        assert list(sig.parameters) == ["file_path", "offset"]
        assert fake_tool.__name__ == "fake_tool"
