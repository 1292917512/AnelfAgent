"""外围工具细节（第八轮：web_download 超时 / python_exec 落盘 / 只读并发标记）。"""

from __future__ import annotations

import json

# 触发目标实体模块导入（@tool 装饰器在导入时注册）
import entities.devops.tools  # noqa: F401
import entities.entity_query.tools  # noqa: F401
import entities.media.tools  # noqa: F401
import entities.model_control.tools  # noqa: F401
import entities.system.tools  # noqa: F401
import entities.ui.tools  # noqa: F401
import entities.web.tools  # noqa: F401
from core.entity import EntityRegistry

_READONLY_TOOLS = [
    "list_models", "get_current_model", "get_model_priority",
    "get_workspace_info", "get_system_info", "get_python_status",
    "list_python_packages", "get_pip_mirror_info", "get_git_config",
    "query_entities", "list_entity_methods", "get_entity_status",
    "get_crash_report", "ui_get_state", "list_voices", "rerank_search",
    "repo_docs", "get_entity_config",
]


class TestRegistryDeclarations:
    def test_readonly_tools_marked_concurrency_safe(self) -> None:
        """只读工具必须标 concurrency_safe——否则与写工具同轮混发时被切进串行批，并行机会流失。"""
        missing = [
            name for name in _READONLY_TOOLS
            if not (EntityRegistry.get(name) and EntityRegistry.get(name).meta.get("concurrency_safe"))
        ]
        assert missing == []

    def test_web_download_timeout_meta(self) -> None:
        """web_download 声明 timeout=300：AI 参数在此范围内生效（不声明则落入全局默认 60s 提前掐断）。"""
        entity = EntityRegistry.get("web_download")
        assert entity is not None
        assert entity.meta.get("timeout") == 300.0


class TestPythonExecPersist:
    def test_large_stdout_persisted(self, monkeypatch) -> None:
        """stdout 超阈值落盘（与 run_shell_command 同一机制），不截断丢弃。"""
        import entities.filesystem.tools as tools

        monkeypatch.setattr(tools, "_load_config", lambda: None)
        monkeypatch.setattr(tools, "_WORKSPACE", "workspace")
        monkeypatch.setattr(tools, "_SANDBOX", False)

        big = "x" * (30_000 + 500)
        result = json.loads(tools.python_exec(f"print({big!r})"))
        assert result["ok"] is True
        assert "persisted" in result
        assert "<persisted-output>" in result["stdout"]
        with open(result["persisted"], encoding="utf-8") as f:
            saved = f.read()
        assert len(saved) >= 30_000

    def test_small_stdout_unchanged(self, monkeypatch) -> None:
        """小输出不落盘（保持原样，返回结构无 persisted 键）。"""
        import entities.filesystem.tools as tools

        monkeypatch.setattr(tools, "_load_config", lambda: None)
        monkeypatch.setattr(tools, "_WORKSPACE", "workspace")
        monkeypatch.setattr(tools, "_SANDBOX", False)

        result = json.loads(tools.python_exec("print('hello')"))
        assert result["ok"] is True
        assert result["stdout"] == "hello"
        assert "persisted" not in result

    def test_stderr_still_capped(self, monkeypatch) -> None:
        """stderr 仍按小限截断（多为回溯/警告，不占用落盘通道）。"""
        import entities.filesystem.tools as tools

        monkeypatch.setattr(tools, "_load_config", lambda: None)
        monkeypatch.setattr(tools, "_WORKSPACE", "workspace")
        monkeypatch.setattr(tools, "_SANDBOX", False)

        result = json.loads(tools.python_exec(
            "import sys; sys.stderr.write('e' * 5000); print('ok')",
        ))
        assert result["stdout"] == "ok"
        assert len(result["stderr"]) < 1200
        assert "截断" in result["stderr"]
