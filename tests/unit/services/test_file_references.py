"""工作区引用的消息标签、历史链接与路径边界。"""
from pathlib import Path
from urllib.parse import quote

import pytest

from core.tags import etag_all, tag_label
from services.chat import clean_message_for_display
from services.file_references import expand_file_references, resolve_reference_path
from services.workspace import WorkspaceService


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {root: tmp_path / root for root in ("workspace", "project")}
    for path in paths.values():
        path.mkdir()
    monkeypatch.setattr(WorkspaceService, "resolve", lambda self, path, root: str(paths[root] / path))
    monkeypatch.setattr(WorkspaceService, "resolve_root", lambda self, root: str(paths[root]))
    return paths


@pytest.mark.parametrize("root", ["workspace", "project"])
@pytest.mark.parametrize("kind", ["file", "dir"])
def test_send_history_reference_roundtrip(roots: dict[str, Path], root: str, kind: str) -> None:
    path = "资料 [草稿]/test (1).txt" if kind == "file" else "资料 [草稿]"
    target = (roots[root] / path).as_posix()
    spec = ("project:" if root == "project" else "") + ("dir:" if kind == "dir" else "") + path
    source = f"请检查 [引用](./{quote(spec, safe='/')})"
    sent = expand_file_references(source)
    assert etag_all(sent) == [(kind, target)]
    displayed = clean_message_for_display({"role": "user", "content": "[uid:web_user]" + sent})["content"]
    assert f"./{quote(spec, safe='/')}" in displayed
    assert expand_file_references(displayed) == sent


@pytest.mark.parametrize("code", [
    "`[a](./note.txt)`", "``literal ` [a](./note.txt)``",
    "```md\n[a](./note.txt)\n```", "~~~md\n[a](./note.txt)\n~~~~",
    "````md\n```\n[a](./note.txt)\n```\n````",
])
def test_reference_parser_leaves_code_literals_untouched(roots: dict[str, Path], code: str) -> None:
    message = code + "\n[a](./note.txt)"
    result = expand_file_references(message)
    assert result.startswith(code + "\n")
    assert result.endswith(tag_label("file", (roots["workspace"] / "note.txt").as_posix()))


def test_unclosed_code_fence_remains_literal(roots: dict[str, Path]) -> None:
    message = "~~~md\n[a](./note.txt)"
    assert expand_file_references(message) == message


@pytest.mark.parametrize("path", ["../secret", "project:../secret", "dir:../../secret", "C:\\secret", "\\\\host\\share", "/etc/passwd", "a\x00b", "a\nb"])
def test_unsafe_reference_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        resolve_reference_path(path)
