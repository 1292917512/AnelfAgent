"""工作区目录树接口（web.routers.workspace.get_tree）单元测试。

覆盖：目录优先排序、隐藏/跳过目录过滤、binary 标记、配额截断标记、
子路径懒加载、目录不存在 404。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.config import ConfigManager
from web.routers import workspace as ws_mod
from web.routers.workspace import get_tree


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("hello")
    (root / "b.txt").write_text("hi")
    (root / "bin.dat").write_bytes(b"\x00\x01")
    (root / ".hidden").write_text("h")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "c.pyc").write_bytes(b"x")
    ConfigManager.set("workspace_root", str(root))
    return root


class TestGetTree:
    async def test_lists_children_dirs_first(self, ws) -> None:
        result = await get_tree(path="", depth=1, root="workspace")
        names = [c["name"] for c in result["children"]]
        assert names[0] == "sub"
        assert "b.txt" in names
        assert result["truncated"] is False

    async def test_skips_hidden_and_skip_dirs(self, ws) -> None:
        result = await get_tree(path="", depth=2, root="workspace")
        names = [c["name"] for c in result["children"]]
        assert ".hidden" not in names
        assert "__pycache__" not in names

    async def test_binary_flag_detected(self, ws) -> None:
        result = await get_tree(path="", depth=1, root="workspace")
        by_name = {c["name"]: c for c in result["children"]}
        assert by_name["b.txt"]["binary"] is False
        assert by_name["bin.dat"]["binary"] is True

    async def test_depth_expands_children(self, ws) -> None:
        result = await get_tree(path="", depth=2, root="workspace")
        sub = next(c for c in result["children"] if c["name"] == "sub")
        assert [c["name"] for c in sub["children"]] == ["a.txt"]

    async def test_subdir_lazy_load(self, ws) -> None:
        result = await get_tree(path="sub", depth=1, root="workspace")
        assert result["path"] == "sub"
        assert [c["name"] for c in result["children"]] == ["a.txt"]

    async def test_truncated_when_budget_exhausted(self, tmp_path, monkeypatch) -> None:
        root = tmp_path / "ws2"
        root.mkdir()
        for i in range(10):
            (root / f"f{i}.txt").write_text("x")
        ConfigManager.set("workspace_root", str(root))
        monkeypatch.setattr(ws_mod, "_TREE_MAX_ENTRIES", 3)
        result = await get_tree(path="", depth=1, root="workspace")
        assert len(result["children"]) == 3
        assert result["truncated"] is True

    async def test_not_found_returns_404(self, ws) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await get_tree(path="nope", depth=1, root="workspace")
        assert exc_info.value.status_code == 404
