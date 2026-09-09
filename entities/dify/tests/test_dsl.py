"""Dify DSL 辅助函数单元测试。"""
from __future__ import annotations

import pytest

from entities.dify.dsl import (
    DslError,
    dsl_diff_stat,
    parse_dsl,
    rename_dsl_app,
    summarize_dsl,
    validate_dsl,
)

_MINIMAL_DSL = """
version: "0.5.0"
kind: app
app:
  name: 测试应用
  mode: workflow
  description: 演示
workflow:
  graph:
    nodes:
      - id: start
        data:
          type: start
      - id: llm1
        data:
          type: llm
    edges: []
  features: {}
"""


def test_validate_minimal():
    summary = validate_dsl(_MINIMAL_DSL)
    assert summary["app_name"] == "测试应用"
    assert summary["mode"] == "workflow"
    assert summary["has_workflow"] is True


def test_validate_missing_name():
    with pytest.raises(DslError, match="app.name"):
        validate_dsl("kind: app\napp:\n  mode: chat\n")


def test_validate_missing_mode():
    with pytest.raises(DslError, match="app.mode"):
        validate_dsl("app:\n  name: x\n")


def test_parse_invalid_yaml():
    with pytest.raises(DslError, match="YAML"):
        parse_dsl("a: [unclosed")


def test_parse_empty():
    with pytest.raises(DslError, match="为空"):
        parse_dsl("   ")


def test_parse_non_dict():
    with pytest.raises(DslError, match="顶层"):
        parse_dsl("- just\n- a\n- list\n")


def test_summarize_nodes():
    summary = summarize_dsl(_MINIMAL_DSL)
    assert summary["node_count"] == 2
    assert set(summary["node_types"]) == {"start", "llm"}


def test_rename():
    renamed = rename_dsl_app(_MINIMAL_DSL, "新名字")
    assert validate_dsl(renamed)["app_name"] == "新名字"
    with pytest.raises(DslError):
        rename_dsl_app(_MINIMAL_DSL, "  ")


def test_diff_stat():
    modified = _MINIMAL_DSL.replace("演示", "改过的描述")
    stat = dsl_diff_stat(_MINIMAL_DSL, modified)
    assert stat["changed"] is True
    assert stat["added"] >= 1 and stat["removed"] >= 1
    same = dsl_diff_stat(_MINIMAL_DSL, _MINIMAL_DSL)
    assert same["changed"] is False
