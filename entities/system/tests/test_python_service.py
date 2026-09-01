"""get_runtime_env_summary 分支与缓存稳定性测试（[运行环境] 人设块数据源）。

注入纪律：摘要只陈述环境事实，禁止包含操作命令教学（事实归系统、决策归 AI）。
"""

from __future__ import annotations

import pytest

from entities.system import python_service


@pytest.fixture(autouse=True)
def _reset_summary_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(python_service, "_runtime_env_summary_cache", None)


def _same_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 shell 的 python3 解析到本进程解释器。"""
    exe = python_service.sys.executable
    monkeypatch.setattr(
        python_service.shutil, "which",
        lambda c: exe if c == "python3" else None,
    )


def test_uv_managed_summary(monkeypatch: pytest.MonkeyPatch):
    _same_interpreter(monkeypatch)
    monkeypatch.setattr(
        python_service, "detect_env_manager",
        lambda p: {"manager": "uv", "uv_managed": True, "uv_version": "0.9"},
    )
    s = python_service.get_runtime_env_summary()
    assert "宿主大环境" in s and "由 uv 创建" in s and "不含 pip" in s
    # 只陈述事实：不出现任何操作命令教学
    assert "uv pip install" not in s and "uv add" not in s
    assert "list_python_packages" not in s and "pip3" not in s
    # 进程级缓存：跨调用字节稳定（人设层指纹依赖）
    assert python_service.get_runtime_env_summary() == s


def test_interpreter_mismatch_reported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        python_service.shutil, "which",
        lambda c: "/usr/bin/python3" if c == "python3" else None,
    )
    s = python_service.get_runtime_env_summary()
    assert "/usr/bin/python3" in s and "不同" in s
    # 解释器不一致时只报身份差异，不延伸装包判断
    assert "不含 pip" not in s


def test_pip_absent_non_uv_summary(monkeypatch: pytest.MonkeyPatch):
    _same_interpreter(monkeypatch)
    monkeypatch.setattr(
        python_service, "detect_env_manager",
        lambda p: {"manager": "pip", "uv_managed": False, "uv_version": None},
    )
    monkeypatch.setattr(python_service.importlib.util, "find_spec", lambda name: None)
    s = python_service.get_runtime_env_summary()
    assert "不含 pip" in s and "由 uv 创建" not in s
