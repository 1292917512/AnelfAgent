"""环境工具的命令回执保真。"""

import json

import pytest

from core.command import CommandResult
from core.tool_results import extract_error_text
from entities.system import python_service, tools


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_package_failure_retains_exit_code_and_stdout(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    monkeypatch.setattr(python_service, f"{action}_packages", lambda packages: CommandResult(False, "resolver failed", "", 3))
    payload = json.loads(getattr(tools, f"{action}_python_packages")("example-package"))
    assert payload["returncode"] == 3 and payload["ok"] is False
    assert "resolver failed" in extract_error_text(payload)
