"""附件路径解析测试（工作区/项目根一致性）。"""

import os

import pytest

from services.chat import resolve_media_path


class TestResolveMediaPath:
    def test_passthrough_urls(self):
        assert resolve_media_path("https://x/y.png") == "https://x/y.png"
        assert resolve_media_path("/api/chat/files/image/a.png") == "/api/chat/files/image/a.png"

    def test_project_prefix_resolves(self, monkeypatch):
        resolved = "/abs/project/README.md"
        monkeypatch.setattr(
            "services.workspace.WorkspaceService.resolve",
            lambda self, path, root: resolved if path == "README.md" else "/nonexist",
        )
        monkeypatch.setattr(os.path, "exists", lambda p: p == resolved)
        assert resolve_media_path("project:README.md") == resolved

    def test_project_prefix_fallback_when_missing(self, monkeypatch):
        monkeypatch.setattr(
            "services.workspace.WorkspaceService.resolve",
            lambda self, path, root: "/nonexist/x",
        )
        assert resolve_media_path("project:nothing.txt") == "project:nothing.txt"

    def test_absolute_and_existing_passthrough(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert resolve_media_path(str(f)) == str(f)

    def test_workspace_relative_resolves(self, monkeypatch):
        monkeypatch.setattr(
            "services.filesystem.safe_workspace_path",
            lambda rel: "/abs/workspace/" + rel,
        )
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/abs/workspace/notes/a.md")
        assert resolve_media_path("notes/a.md") == "/abs/workspace/notes/a.md"
