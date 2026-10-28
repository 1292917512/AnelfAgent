"""镜像覆盖只作用于安装子进程，不修改项目索引、其他工具来源或全局配置。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.environment import Environment
from app.settings import DOWNLOADS, Layout, Settings, download_profile


@pytest.mark.parametrize("source", list(DOWNLOADS["pypi_sources"]))
def test_package_source_is_independent_and_read_only(tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path)
    layout.save_settings(Settings(download_source="global", pypi_source=source))
    original = b'[tool.uv]\nindex-url="https://example.invalid/simple"\n'
    project = tmp_path / "pyproject.toml"; project.write_bytes(original)
    profile = download_profile(layout.load_settings())
    assert profile["pypi"] == DOWNLOADS["pypi_sources"][source]["url"]
    assert profile["node"] == DOWNLOADS["global"]["node"]
    assert profile["python"] == DOWNLOADS["global"]["python"]
    for key in DOWNLOADS["uv_index_environment"]:
        monkeypatch.setenv(key, "existing-value")
    environment = Environment(layout, lambda _: None)
    isolated = environment.environ(isolated_index=True)
    assert all(key not in isolated and os.environ[key] == "existing-value" for key in DOWNLOADS["uv_index_environment"])
    assert isolated["UV_CACHE_DIR"] == environment.environ()["UV_CACHE_DIR"]
    assert project.read_bytes() == original


def test_existing_settings_keep_automatic_source() -> None:
    assert Settings.parse({"download_source": "china"}).pypi_source == "auto"
    assert download_profile(Settings(download_source="china"))["pypi"] == DOWNLOADS["china"]["pypi"]
    with pytest.raises(ValueError, match="Unknown uv"):
        Settings.parse({"pypi_source": "https://example.invalid"})
