"""SSH 操作态势（ops_state）测试：按 scope/连接记录、TTL、远程文档缓存与抓取命令。"""

from __future__ import annotations

import time

import pytest

from entities._sdk import ToolOp
from entities.ssh import ops_state
from entities.ssh.manager import STATUS_CONNECTED

_SCOPE = "user_test:1"


class _FakeManager:
    """get_snapshot 替身（避免触达真实连接池与凭据存储）。"""

    def __init__(self, snapshots: dict) -> None:
        self._snapshots = snapshots

    def get_snapshot(self, name: str):
        return self._snapshots.get(name)


@pytest.fixture()
def state(monkeypatch):
    ops_state._scoped.clear()
    ops_state._doc_cache.clear()
    ops_state._doc_inflight.clear()
    fake = _FakeManager({
        "web": {"name": "web", "host": "192.168.1.10", "username": "root",
                "status": STATUS_CONNECTED, "work_dir": "/var/www"},
    })
    monkeypatch.setattr(ops_state, "get_ssh_manager", lambda: fake)
    yield fake
    ops_state._scoped.clear()
    ops_state._doc_cache.clear()
    ops_state._doc_inflight.clear()


def _op(kind: str = "ssh_exec", target: str = "ls", conn: str = "web",
        ok: bool = True, note: str = "", scope: str = _SCOPE) -> ToolOp:
    return ToolOp(
        scope=scope, tool=kind, target=target,
        targets=(target,) if target else (),
        arguments={"name": conn}, ok=ok, note=note, duration_ms=5,
    )


class TestRecordAndRender:
    def test_no_session_renders_none(self, state) -> None:
        assert ops_state.render_scope(_SCOPE) is None

    def test_render_contains_conn_dir_and_ops(self, state) -> None:
        ops_state.record_tool_op(_op(target="systemctl restart app"))
        ops_state.record_tool_op(_op(kind="ssh_upload",
                                     target="/tmp/a.tgz → /var/www/a.tgz"))
        text = ops_state.render_scope(_SCOPE)
        assert text is not None
        assert "[SSH 操作态势]" in text
        assert "── web (root@192.168.1.10)（在线） ──" in text
        assert "远程目录: /var/www" in text
        assert "ssh_exec systemctl restart app ✓" in text
        assert "ssh_upload /tmp/a.tgz → /var/www/a.tgz ✓" in text

    def test_only_operated_connections_shown(self, state, monkeypatch) -> None:
        ops_state.record_tool_op(_op(conn="web"))
        text = ops_state.render_scope(_SCOPE)
        assert text is not None
        assert "db" not in text

    def test_scope_isolation(self, state) -> None:
        ops_state.record_tool_op(_op(scope="user_a:1"))
        assert ops_state.render_scope("user_b:2") is None

    def test_failed_op_with_note(self, state) -> None:
        ops_state.record_tool_op(_op(ok=False, note="退出码 127"))
        text = ops_state.render_scope(_SCOPE)
        assert text is not None
        assert "ssh_exec ls ✗（退出码 127）" in text

    def test_ttl_expiry_drops_session(self, state) -> None:
        ops_state.record_tool_op(_op())
        ops_state._scoped[_SCOPE]["web"].last_active = time.time() - 700
        assert ops_state.render_scope(_SCOPE) is None
        assert _SCOPE not in ops_state._scoped

    def test_ops_trimmed_to_max(self, state, monkeypatch) -> None:
        monkeypatch.setattr(
            ops_state, "get_config_int",
            lambda key, default=0: 3 if key == "ssh_ops_max_entries" else default,
        )
        for i in range(5):
            ops_state.record_tool_op(_op(target=f"cmd{i}"))
        text = ops_state.render_scope(_SCOPE)
        assert text is not None
        assert "cmd4" in text and "cmd1" not in text

    def test_unknown_conn_renders_without_snapshot(self, state) -> None:
        ops_state.record_tool_op(_op(conn="gone"))
        text = ops_state.render_scope(_SCOPE)
        assert text is not None
        assert "── gone ──" in text


class TestDocFetchCommand:
    def test_sanitize_doc_names(self) -> None:
        assert ops_state.sanitize_doc_names("AGENTS.md, ../etc/passwd, README.md,,") == [
            "AGENTS.md", "README.md",
        ]
        assert ops_state.sanitize_doc_names("$(rm -rf /),`id`,a b.md") == []

    def test_build_and_parse_roundtrip(self) -> None:
        command = ops_state.build_doc_fetch_command(
            "/var/www", ["AGENTS.md", "README.md"], 3000,
        )
        assert command.startswith("cd /var/www && ")
        assert "[ -f AGENTS.md ]" in command
        assert "head -c 3000 AGENTS.md" in command

        fake_output = (
            "__ANELF_DOC_BEGIN__AGENTS.md\n# 部署约定\n先备份。\n__ANELF_DOC_END__\n"
            "__ANELF_DOC_BEGIN__README.md\n介绍\n\n__ANELF_DOC_END__\n"
        )
        docs = ops_state.parse_doc_fetch_output(fake_output)
        assert docs == {"AGENTS.md": "# 部署约定\n先备份。", "README.md": "介绍"}

    def test_parse_empty_output(self) -> None:
        assert ops_state.parse_doc_fetch_output("") == {}


class TestDocRefresh:
    async def test_refresh_writes_cache(self, state, monkeypatch) -> None:
        class _CaptureManager(_FakeManager):
            async def run_capture(self, name, command, timeout=15.0):
                return "__ANELF_DOC_BEGIN__AGENTS.md\n约定内容\n__ANELF_DOC_END__\n"

        monkeypatch.setattr(ops_state, "get_ssh_manager",
                            lambda: _CaptureManager({}))
        await ops_state._refresh_docs("web", "/var/www")
        cached = ops_state._doc_cache["web"]["/var/www"]
        assert cached.docs == {"AGENTS.md": "约定内容"}

    async def test_refresh_failure_silent(self, state, monkeypatch) -> None:
        class _FailManager(_FakeManager):
            async def run_capture(self, name, command, timeout=15.0):
                return None

        monkeypatch.setattr(ops_state, "get_ssh_manager", lambda: _FailManager({}))
        await ops_state._refresh_docs("web", "/var/www")
        assert ops_state._doc_cache == {}
        assert ("web", "/var/www") not in ops_state._doc_inflight

    def test_docs_rendered_from_cache(self, state) -> None:
        ops_state._doc_cache["web"] = {
            "/var/www": ops_state._DirDocs(time.time(), {"AGENTS.md": "缓存文档"}),
        }
        ops_state.record_tool_op(_op())
        text = ops_state.render_scope(_SCOPE)
        assert text is not None
        assert "## /var/www/AGENTS.md" in text
        assert "缓存文档" in text
