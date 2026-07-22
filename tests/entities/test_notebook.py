"""B7 NotebookEdit 测试。"""

from __future__ import annotations

import json

import pytest

from entities.filesystem import tools
from entities.filesystem.notebook import notebook_edit


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_load_config", lambda: None)
    monkeypatch.setattr(tools, "_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(tools, "_SANDBOX", True)
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# 标题\n"]},
            {"cell_type": "code", "metadata": {}, "source": ["print(1)\n"],
             "outputs": [], "execution_count": 1},
        ],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }
    (tmp_path / "test.ipynb").write_text(json.dumps(nb))
    yield tmp_path


class TestNotebookEdit:
    def test_replace_cell(self, workspace):
        out = json.loads(notebook_edit("test.ipynb", 1, "print(42)"))
        assert out["ok"]
        nb = json.loads((workspace / "test.ipynb").read_text())
        assert nb["cells"][1]["source"] == ["print(42)"]
        assert nb["cells"][1]["cell_type"] == "code"

    def test_insert_cell(self, workspace):
        out = json.loads(notebook_edit("test.ipynb", 1, "新内容", cell_type="markdown",
                                       edit_mode="insert"))
        assert out["ok"]
        nb = json.loads((workspace / "test.ipynb").read_text())
        assert len(nb["cells"]) == 3
        assert nb["cells"][1]["cell_type"] == "markdown"

    def test_delete_cell(self, workspace):
        out = json.loads(notebook_edit("test.ipynb", 0, edit_mode="delete"))
        assert out["ok"]
        nb = json.loads((workspace / "test.ipynb").read_text())
        assert len(nb["cells"]) == 1

    def test_index_out_of_range(self, workspace):
        out = json.loads(notebook_edit("test.ipynb", 99, "x"))
        assert "越界" in out["error"]

    def test_bad_mode(self, workspace):
        out = json.loads(notebook_edit("test.ipynb", 0, "x", edit_mode="append"))
        assert "未知" in out["error"]

    def test_non_ipynb_rejected(self, workspace):
        (workspace / "a.txt").write_text("x")
        out = json.loads(notebook_edit("a.txt", 0, "x"))
        assert "仅支持" in out["error"]


class TestDispatch:
    def test_read_file_ipynb_summary(self, workspace):
        out = tools.read_file("test.ipynb")
        assert "共 2 个 cell" in out
        assert "cell[0] (markdown)" in out
        assert "notebook_edit" in out

    def test_edit_file_ipynb_redirected(self, workspace):
        out = json.loads(tools.edit_file("test.ipynb", "print(1)", "print(2)"))
        assert out["code"] == 5
        assert "notebook_edit" in out["error"]
