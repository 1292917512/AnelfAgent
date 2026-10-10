"""工具索引键、实体路径声明与界面文件定位的一致性。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.memory import notes
from core.entity import EntityRegistry
from core.tool_registry import activate_group, deferred_tool, tool
from services.activity_presentation import tool_targets


@pytest.mark.parametrize("deferred", [False, True])
async def test_module_path_resolver_keeps_tool_schema_and_arguments(tmp_path, monkeypatch, deferred):
    project = tmp_path / "project"
    data = project / "config" / "memory"
    data.mkdir(parents=True)
    (data / "note.md").write_text("AI-owned note", encoding="utf-8")
    monkeypatch.setattr(notes, "_memory_dir", data)
    monkeypatch.setattr("services.activity_presentation.project_root", lambda: str(project))
    monkeypatch.setattr("services.filesystem.workspace_root", lambda: str(project / "workspace"))

    decorator = deferred_tool if deferred else tool

    @decorator(name="path_probe", group="path_probe", path_resolver=notes.resolve_memory_file)
    async def read_note(file_path: str) -> str:
        return notes.read_memory_file(file_path)

    if deferred:
        activate_group("path_probe")
    try:
        schema = EntityRegistry.get_tool_schema_by_names(["path_probe"])[0]
        assert "path_resolver" not in json.dumps(schema)
        assert list(schema["function"]["parameters"]["properties"]) == ["file_path", "_timeout"]
        assert await EntityRegistry.execute_tool("path_probe", '{"file_path":"memory/note.md"}') == "AI-owned note"
        assert tool_targets('{"file_path":"memory/note.md"}', "path_probe") == [{
            "key": "file_path", "value": "memory/note.md", "root": "project", "path": "config/memory/note.md",
        }]
        assert tool_targets('{"file_path":"memory/../../outside.md"}', "path_probe") == [{
            "key": "file_path", "value": "memory/../../outside.md", "root": "unresolved",
        }]
        external = tmp_path / "external-data"
        monkeypatch.setattr(notes, "_memory_dir", external)
        assert tool_targets('{"file_path":"memory/note.md"}', "path_probe")[0] == {
            "key": "file_path", "value": "memory/note.md", "root": "external", "path": str(external / "note.md"),
        }
    finally:
        EntityRegistry.unregister("path_probe")


def test_plain_workspace_paths_are_not_inferred_as_memory(tmp_path, monkeypatch):
    monkeypatch.setattr("services.filesystem.workspace_root", lambda: str(tmp_path / "workspace"))
    assert tool_targets('{"path":"memory/note.md"}') == [{"key": "path", "value": "memory/note.md"}]
    absolute = tmp_path / "workspace" / "note.md"
    assert tool_targets(json.dumps({"path": str(absolute)}))[0] == {
        "key": "path", "value": str(absolute), "root": "workspace", "path": "note.md",
    }


def test_failed_resolver_does_not_break_activity(monkeypatch):
    def unavailable(_: str) -> Path:
        raise RuntimeError("Provider unavailable")

    @tool(name="broken_path_probe", path_resolver=unavailable)
    def read_note(file_path: str) -> str:
        return file_path

    try:
        assert tool_targets('{"file_path":"memory/note.md"}', "broken_path_probe")[0]["root"] == "unresolved"
    finally:
        EntityRegistry.unregister("broken_path_probe")
