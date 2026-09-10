"""工作区目录树与文件操作接口（web.routers.workspace）单元测试。

覆盖：目录优先排序、隐藏/跳过目录过滤、binary 标记、has_children 标记、
顶层不受全局配额限制（深层可逐层展开）、递归配额截断标记、单层上限截断、
子路径懒加载、目录不存在 404、move（重命名/移动）、upload（上传）。
"""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from core.config import ConfigManager
from web.routers import workspace as ws_mod
from web.routers.workspace import MoveRequest, get_tree, move_entry, upload_file


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

    async def test_has_children_flag(self, ws) -> None:
        result = await get_tree(path="", depth=1, root="workspace")
        by_name = {c["name"]: c for c in result["children"]}
        assert by_name["sub"]["has_children"] is True
        (ws / "empty").mkdir()
        result = await get_tree(path="", depth=1, root="workspace")
        by_name = {c["name"]: c for c in result["children"]}
        assert by_name["empty"]["has_children"] is False

    async def test_depth_expands_children(self, ws) -> None:
        result = await get_tree(path="", depth=2, root="workspace")
        sub = next(c for c in result["children"] if c["name"] == "sub")
        assert [c["name"] for c in sub["children"]] == ["a.txt"]

    async def test_subdir_lazy_load(self, ws) -> None:
        result = await get_tree(path="sub", depth=1, root="workspace")
        assert result["path"] == "sub"
        assert [c["name"] for c in result["children"]] == ["a.txt"]

    async def test_top_level_not_limited_by_global_budget(self, tmp_path, monkeypatch) -> None:
        """请求目标的直接子级不受全局配额限制——浅层兄弟再多也不会吞掉深层分支。"""
        root = tmp_path / "ws2"
        root.mkdir()
        for i in range(10):
            (root / f"d{i}").mkdir()
            (root / f"d{i}" / "f.txt").write_text("x")
        ConfigManager.set("workspace_root", str(root))
        monkeypatch.setattr(ws_mod, "_TREE_MAX_ENTRIES", 3)
        result = await get_tree(path="", depth=2, root="workspace")
        # 10 个顶层目录全部可见（free 层），递归预取受配额截断
        assert len(result["children"]) == 10
        assert result["truncated"] is True

    async def test_single_dir_cap(self, tmp_path, monkeypatch) -> None:
        """单层目录超过 _DIR_MAX_CHILDREN 时截断并标记。"""
        root = tmp_path / "ws3"
        root.mkdir()
        for i in range(5):
            (root / f"f{i}.txt").write_text("x")
        ConfigManager.set("workspace_root", str(root))
        monkeypatch.setattr(ws_mod, "_DIR_MAX_CHILDREN", 3)
        result = await get_tree(path="", depth=1, root="workspace")
        assert len(result["children"]) == 3
        assert result["truncated"] is True

    async def test_not_found_returns_404(self, ws) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await get_tree(path="nope", depth=1, root="workspace")
        assert exc_info.value.status_code == 404


class TestMove:
    async def test_rename_file(self, ws) -> None:
        result = await move_entry(MoveRequest(src="b.txt", dst="b2.txt"))
        assert result["path"] == "b2.txt"
        assert (ws / "b2.txt").read_text() == "hi"
        assert not (ws / "b.txt").exists()

    async def test_move_into_dir(self, ws) -> None:
        result = await move_entry(MoveRequest(src="b.txt", dst="sub/b.txt"))
        assert result["path"] == "sub/b.txt"
        assert (ws / "sub" / "b.txt").exists()

    async def test_move_dir(self, ws) -> None:
        (ws / "target").mkdir()
        result = await move_entry(MoveRequest(src="sub", dst="target/sub"))
        assert result["path"] == "target/sub"
        assert (ws / "target" / "sub" / "a.txt").exists()

    async def test_dst_exists_conflict(self, ws) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await move_entry(MoveRequest(src="b.txt", dst="bin.dat"))
        assert exc_info.value.status_code == 409

    async def test_src_missing_404(self, ws) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await move_entry(MoveRequest(src="nope.txt", dst="x.txt"))
        assert exc_info.value.status_code == 404

    async def test_move_dir_into_itself_rejected(self, ws) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await move_entry(MoveRequest(src="sub", dst="sub/inner"))
        assert exc_info.value.status_code == 400

    async def test_dst_parent_missing_404(self, ws) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await move_entry(MoveRequest(src="b.txt", dst="ghost/b.txt"))
        assert exc_info.value.status_code == 404


class TestUpload:
    async def test_upload_file(self, ws) -> None:
        upload = UploadFile(file=io.BytesIO(b"hello upload"), filename="up.txt")
        result = await upload_file(file=upload, dir="", root="workspace")
        assert result["path"] == "up.txt"
        assert (ws / "up.txt").read_bytes() == b"hello upload"

    async def test_upload_into_subdir(self, ws) -> None:
        upload = UploadFile(file=io.BytesIO(b"x"), filename="n.bin")
        result = await upload_file(file=upload, dir="sub", root="workspace")
        assert result["path"] == "sub/n.bin"

    async def test_upload_conflict_409(self, ws) -> None:
        upload = UploadFile(file=io.BytesIO(b"x"), filename="b.txt")
        with pytest.raises(HTTPException) as exc_info:
            await upload_file(file=upload, dir="", root="workspace")
        assert exc_info.value.status_code == 409

    async def test_upload_oversize_413(self, ws, monkeypatch) -> None:
        monkeypatch.setattr(ws_mod, "_MAX_UPLOAD_BYTES", 4)
        upload = UploadFile(file=io.BytesIO(b"0123456789"), filename="big.bin")
        with pytest.raises(HTTPException) as exc_info:
            await upload_file(file=upload, dir="", root="workspace")
        assert exc_info.value.status_code == 413
        assert not (ws / "big.bin").exists()

    async def test_upload_bad_name_400(self, ws) -> None:
        upload = UploadFile(file=io.BytesIO(b"x"), filename=".secret")
        with pytest.raises(HTTPException) as exc_info:
            await upload_file(file=upload, dir="", root="workspace")
        assert exc_info.value.status_code == 400
