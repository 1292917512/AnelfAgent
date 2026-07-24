"""_env_info_block 平台命令方言提示测试（BSD 用户态注入 / Linux 不注入）。"""

from __future__ import annotations

import platform

import pytest

from agent.mind import prefrontal_cortex


def _block(monkeypatch: pytest.MonkeyPatch, system: str) -> str:
    monkeypatch.setattr(platform, "system", lambda: system)
    return prefrontal_cortex._env_info_block()


def test_bsd_dialect_hint_on_darwin(monkeypatch: pytest.MonkeyPatch):
    block = _block(monkeypatch, "Darwin")
    assert "平台: darwin" in block
    assert "BSD" in block
    assert "-printf" in block


def test_no_bsd_dialect_hint_on_linux(monkeypatch: pytest.MonkeyPatch):
    block = _block(monkeypatch, "Linux")
    assert "平台: linux" in block
    assert "BSD" not in block
