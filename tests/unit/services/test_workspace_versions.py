from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services.workspace import WorkspaceError, WorkspaceService


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkspaceService:
    monkeypatch.setattr(WorkspaceService, "resolve", lambda self, path, root: str(tmp_path / path))
    monkeypatch.setattr(WorkspaceService, "resolve_root", lambda self, root: str(tmp_path))
    return WorkspaceService()


def test_external_edit_is_not_overwritten(workspace: WorkspaceService, tmp_path: Path) -> None:
    file = tmp_path / "note.txt"
    file.write_text("before", encoding="utf-8")
    version = workspace.read_file("note.txt", "workspace")["version"]
    file.write_text("external", encoding="utf-8")
    with pytest.raises(WorkspaceError) as error:
        workspace.write_file("note.txt", "draft", "workspace", version)
    assert error.value.status_code == 409
    assert file.read_text(encoding="utf-8") == "external"


def test_only_one_concurrent_save_uses_same_version(workspace: WorkspaceService, tmp_path: Path) -> None:
    initial = workspace.write_file("note.txt", "before", "workspace", None)

    def save(text: str) -> int:
        try:
            workspace.write_file("note.txt", text, "workspace", initial["version"])
            return 200
        except WorkspaceError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ["first", "second"])) == [200, 409]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") in {"first", "second"}


def test_create_collision_and_deleted_version(workspace: WorkspaceService, tmp_path: Path) -> None:
    initial = workspace.write_file("note.txt", "before", "workspace", None)
    with pytest.raises(WorkspaceError, match="修改"):
        workspace.write_file("note.txt", "collision", "workspace", None)
    (tmp_path / "note.txt").unlink()
    with pytest.raises(WorkspaceError, match="修改"):
        workspace.write_file("note.txt", "stale", "workspace", initial["version"])
    assert not (tmp_path / "note.txt").exists()


def test_save_preserves_bom_and_line_endings(workspace: WorkspaceService, tmp_path: Path) -> None:
    file = tmp_path / "note.txt"
    file.write_bytes(b"\xef\xbb\xbfone\r\ntwo")
    initial = workspace.read_file("note.txt", "workspace")
    assert initial["content"] == "one\ntwo"
    saved = workspace.write_file("note.txt", "three\nfour", "workspace", initial["version"])
    assert file.read_bytes() == b"\xef\xbb\xbfthree\r\nfour"
    assert saved["content"] == "three\nfour"
    assert saved["version"] != initial["version"]


def test_failed_atomic_replace_leaves_original(workspace: WorkspaceService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "note.txt"
    file.write_text("before", encoding="utf-8")
    initial = workspace.read_file("note.txt", "workspace")

    def fail(*args: object) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr("core.file_utils.os.replace", fail)
    with pytest.raises(WorkspaceError):
        workspace.write_file("note.txt", "draft", "workspace", initial["version"])
    assert file.read_text(encoding="utf-8") == "before"
    assert list(tmp_path.iterdir()) == [file]


def test_non_utf8_file_is_readonly(workspace: WorkspaceService, tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_bytes(b"\xff\xfe\x00")
    initial = workspace.read_file("note.txt", "workspace")
    assert initial["binary"]
    with pytest.raises(WorkspaceError) as error:
        workspace.write_file("note.txt", "draft", "workspace", initial["version"])
    assert error.value.status_code == 415
