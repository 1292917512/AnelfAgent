"""访问范围与原 Web 密码的共享配置；全部使用临时项目，不启动真实服务。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.manager import Manager
from app.settings import Layout, Settings
from app.web_access import configure_access


def test_password_updates_preserve_web_config_and_never_echo(tmp_path: Path) -> None:
    path = tmp_path / "webui.json"
    assert not configure_access(path)["password_set"]
    assert not path.exists()
    original = {"server": {"port": 9000}, "auth": {"api_keys": [{"id": "existing"}], "strict": True}}
    path.write_text(json.dumps(original), "utf-8")
    result = configure_access(path, "test-password")
    assert result == {"available": True, "password_set": True}
    saved = json.loads(path.read_text("utf-8"))
    assert saved["server"] == original["server"]
    assert saved["auth"]["api_keys"] == original["auth"]["api_keys"]
    assert saved["auth"]["strict"] is True
    assert saved["auth"]["password"] == "test-password"
    assert "test-password" not in json.dumps(configure_access(path))
    assert not configure_access(path, "")["password_set"]
    assert json.loads(path.read_text("utf-8"))["auth"]["strict"] is False


@pytest.mark.parametrize("content", ["{broken", "[]", '{"auth":[]}', '{"auth":{"password":123}}'])
def test_invalid_existing_web_config_is_not_overwritten(tmp_path: Path, content: str) -> None:
    path = tmp_path / "webui.json"
    path.write_text(content, "utf-8")
    with pytest.raises(ValueError):
        configure_access(path, "test-password")
    assert path.read_text("utf-8") == content


def test_manager_uses_project_config_paths_and_preserves_explicit_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path / "project")
    core = layout.root / "core"
    core.mkdir()
    (core / "__init__.py").write_text("", "utf-8")
    (core / "path.py").write_text(
        "import os\nclass ConfigPaths:\n WEBUI_CONFIG=os.path.join(os.environ['ANELF_CONFIG_DIR'],'webui.json')\n", "utf-8"
    )
    frontend = layout.root / "web/frontend/dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("ready", "utf-8")
    custom = tmp_path / "custom-config"
    monkeypatch.setenv("ANELF_CONFIG_DIR", str(custom))
    monkeypatch.setattr(Layout, "python", property(lambda _: Path(sys.executable)))
    manager = Manager(layout)
    assert manager.settings.web_host == "0.0.0.0"
    assert manager.web_url == "http://127.0.0.1:8092/webui/"
    assert Settings.parse({"web_host": "127.0.0.1"}).web_host == "127.0.0.1"
    assert not manager.web_access()["password_set"]
    assert not custom.exists()
    assert manager.web_access("fixture-password")["password_set"]
    assert json.loads((custom / "webui.json").read_text("utf-8"))["auth"]["password"] == "fixture-password"
    assert "fixture-password" not in json.dumps(manager.status())
    assert "fixture-password" not in json.dumps(manager.web_access())
    with manager._operation, pytest.raises(RuntimeError, match="Stop the application"):
        manager.web_access("")
    assert not manager.web_access("")["password_set"]
