"""用户 hook 事件面（agent/hooks + run_command stdin 扩展）单元测试。"""

from __future__ import annotations

import json
import sys

import pytest

from agent.hooks import get_hook_registry, hooks_active, reload_hooks, run_event_hooks
from agent.hooks.runner import HookRegistry
from core.command import run_command


def _write_hooks(path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class TestRunCommandStdin:
    async def test_stdin_roundtrip(self) -> None:
        # cat 原样回显 stdin（POSIX；Windows 跳过）
        if sys.platform == "win32":
            pytest.skip("POSIX only")
        result = await run_command.async_version("cat", timeout_sec=10, stdin_data="hello-hook")
        assert result.ok and result.stdout == "hello-hook"

    async def test_returncode_populated(self) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX only")
        ok = await run_command.async_version("true", timeout_sec=10)
        fail = await run_command.async_version("false", timeout_sec=10)
        assert ok.returncode == 0
        assert fail.returncode == 1 and not fail.ok


class TestHookRegistryLoad:
    def test_valid_config(self, tmp_path) -> None:
        p = tmp_path / "hooks.json"
        _write_hooks(p, {"tool_pre": [{"matcher": "run_shell_command*", "command": "echo hi"}],
                         "reply_end": [{"command": "echo done"}]})
        reg = HookRegistry()
        assert reg.load(str(p)) == 2
        assert len(reg.for_event("tool_pre")) == 1
        assert reg.for_event("tool_pre")[0].matcher == "run_shell_command*"

    def test_unknown_event_rejected(self, tmp_path) -> None:
        p = tmp_path / "hooks.json"
        _write_hooks(p, {"bogus_event": [{"command": "x"}]})
        with pytest.raises(ValueError):
            HookRegistry().load(str(p))

    def test_missing_command_rejected(self, tmp_path) -> None:
        p = tmp_path / "hooks.json"
        _write_hooks(p, {"tool_pre": [{"matcher": "*"}]})
        with pytest.raises(ValueError):
            HookRegistry().load(str(p))

    def test_timeout_clamped(self, tmp_path) -> None:
        p = tmp_path / "hooks.json"
        _write_hooks(p, {"tool_pre": [{"command": "x", "timeout": 9999}]})
        reg = HookRegistry()
        reg.load(str(p))
        assert reg.for_event("tool_pre")[0].timeout == 60.0


class TestReloadHooks:
    def test_missing_file_clears(self, tmp_path) -> None:
        assert reload_hooks(str(tmp_path / "nonexistent.json")) == 0
        assert get_hook_registry().empty()

    def test_bad_file_preserves_last_good(self, tmp_path) -> None:
        good = tmp_path / "good.json"
        _write_hooks(good, {"tool_pre": [{"command": "echo 1"}]})
        assert reload_hooks(str(good)) == 1
        bad = tmp_path / "bad.json"
        _write_hooks(bad, {"tool_pre": "not-a-list"})
        assert reload_hooks(str(bad)) == -1
        # 保留上次成功集
        assert len(get_hook_registry().for_event("tool_pre")) == 1
        # 清理：恢复空注册表，避免影响其他测试
        reload_hooks(str(tmp_path / "nonexistent.json"))

    def test_hooks_active_empty_registry_false(self, tmp_path) -> None:
        reload_hooks(str(tmp_path / "nonexistent.json"))
        assert hooks_active("tool_pre") is False


class TestRunEventHooks:
    @pytest.fixture(autouse=True)
    def _cleanup(self, tmp_path):
        reload_hooks(str(tmp_path / "nonexistent.json"))
        yield
        reload_hooks(str(tmp_path / "nonexistent.json"))

    def _load(self, tmp_path, hooks: dict) -> None:
        p = tmp_path / "hooks.json"
        _write_hooks(p, hooks)
        reload_hooks(str(p))

    async def test_exit_zero_allows(self, tmp_path) -> None:
        self._load(tmp_path, {"tool_pre": [{"command": "exit 0"}]})
        out = await run_event_hooks("tool_pre", tool_name="read_file")
        assert out.allowed and out.executed == 1

    async def test_exit_two_blocks_with_reason(self, tmp_path) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX only")
        self._load(tmp_path, {"tool_pre": [{"command": "echo '不许删' >&2; exit 2"}]})
        out = await run_event_hooks("tool_pre", tool_name="delete_file")
        assert not out.allowed
        assert "不许删" in out.reason
        assert out.blocked_by

    async def test_exit_one_non_blocking(self, tmp_path) -> None:
        self._load(tmp_path, {"tool_pre": [{"command": "exit 1"}]})
        out = await run_event_hooks("tool_pre", tool_name="read_file")
        assert out.allowed and out.executed == 1  # 失败但不阻塞

    async def test_timeout_non_blocking(self, tmp_path) -> None:
        self._load(tmp_path, {"tool_pre": [{"command": "sleep 5", "timeout": 1}]})
        out = await run_event_hooks("tool_pre", tool_name="read_file")
        assert out.allowed  # 超时视为非阻塞错误

    async def test_matcher_filters(self, tmp_path) -> None:
        self._load(tmp_path, {"tool_pre": [{"matcher": "run_shell_command*", "command": "exit 0"}]})
        out = await run_event_hooks("tool_pre", tool_name="read_file")
        assert out.executed == 0  # 不匹配：未执行

    async def test_deny_wins_over_allow(self, tmp_path) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX only")
        self._load(tmp_path, {"tool_pre": [
            {"command": "exit 0"},
            {"command": "echo blocked-by-second >&2; exit 2"},
        ]})
        out = await run_event_hooks("tool_pre", tool_name="read_file")
        assert not out.allowed and "blocked-by-second" in out.reason

    async def test_stdin_payload_delivered(self, tmp_path) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX only")
        out_file = tmp_path / "payload.json"
        self._load(tmp_path, {"tool_post": [{"command": f"cat > {out_file}"}]})
        await run_event_hooks("tool_post", tool_name="write_file", arguments='{"path": "/x"}')
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["event"] == "tool_post"
        assert data["tool_name"] == "write_file"
