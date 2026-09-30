"""附件路径解析测试（工作区/项目根一致性）。"""

import os

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

    def test_dir_prefix_parses_as_workspace(self, monkeypatch):
        monkeypatch.setattr(
            "services.workspace.WorkspaceService.resolve",
            lambda self, path, root: f"/abs/workspace/{path}" if root == "workspace" else "/nonexist",
        )
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/abs/workspace/docs")
        assert resolve_media_path("dir:docs") == "/abs/workspace/docs"

    def test_project_dir_prefix_parses(self, monkeypatch):
        resolved = "/abs/project/src"
        monkeypatch.setattr(
            "services.workspace.WorkspaceService.resolve",
            lambda self, path, root: resolved if (path == "src" and root == "project") else "/nonexist",
        )
        monkeypatch.setattr(os.path, "exists", lambda p: p == resolved)
        assert resolve_media_path("project:dir:src") == resolved

    def test_workspace_relative_resolves(self, monkeypatch):
        monkeypatch.setattr(
            "services.workspace.WorkspaceService.resolve",
            lambda self, path, root: f"/abs/workspace/{path}" if root == "workspace" else "/nonexist",
        )
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/abs/workspace/notes/a.md")
        assert resolve_media_path("notes/a.md") == "/abs/workspace/notes/a.md"


class TestFileRefDescs:
    def test_dir_and_file_markers(self):
        from services.chat import _file_ref_descs
        descs = _file_ref_descs([
            ("/abs/project/src", True),
            ("/abs/workspace/notes/a.md", False),
        ])
        assert descs == "[dir:/abs/project/src] [file:/abs/workspace/notes/a.md]"

    def test_file_only(self):
        from services.chat import _file_ref_descs
        assert _file_ref_descs([("/abs/x.png", False)]) == "[file:/abs/x.png]"


class TestRelativePathNotCwdHijacked:
    """相对路径的存在性判定不得以进程 cwd 为根（项目根下同名条目会劫持工作区相对路径）。"""

    def test_relative_resolved_to_workspace_even_if_cwd_has_same_name(self, monkeypatch, tmp_path):
        # cwd（项目根）下存在同名目录 tmp —— 仍须解析到工作区绝对路径
        import os
        resolved = "/abs/workspace/tmp"
        monkeypatch.setattr(
            "services.workspace.WorkspaceService.resolve",
            lambda self, path, root: resolved if root == "workspace" else "/nonexist",
        )
        monkeypatch.setattr(os.path, "exists", lambda p: p == resolved)
        assert resolve_media_path("tmp") == resolved

    def test_project_prefix_routes_to_project_root(self, monkeypatch):
        import os
        resolved = "/abs/project/tmp"
        monkeypatch.setattr(
            "services.workspace.WorkspaceService.resolve",
            lambda self, path, root: resolved if (path == "tmp" and root == "project") else "/nonexist",
        )
        monkeypatch.setattr(os.path, "exists", lambda p: p == resolved)
        assert resolve_media_path("project:tmp") == resolved
