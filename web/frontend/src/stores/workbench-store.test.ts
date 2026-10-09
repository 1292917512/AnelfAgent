import { beforeEach, describe, expect, it } from "vitest";
import { workspaceFileId } from "@/lib/workspace-file";
import { useWorkbenchStore } from "./workbench-store";
import { useFileEditorStore } from "./file-editor-store";

const workspace = { root: "workspace" as const, path: "docs/note.md" };
const project = { root: "project" as const, path: "docs/note.md" };
const file = { path: workspace.path, name: "note.md", content: "disk", version: "v1", size: 4, modified: 0, binary: false, truncated: false };

beforeEach(() => {
  useWorkbenchStore.getState().closeAllFiles();
  useFileEditorStore.setState({ tabs: new Map() });
});

describe("workspace file identities", () => {
  it("keeps identically named files in different roots separate", () => {
    const workbench = useWorkbenchStore.getState();
    workbench.openFile(workspace.path, workspace.root);
    workbench.openFile(project.path, project.root);
    expect(useWorkbenchStore.getState().openFiles).toEqual([workspace, project]);
    workbench.closeFile(workspaceFileId(workspace));
    expect(useWorkbenchStore.getState().openFiles).toEqual([project]);
    expect(useWorkbenchStore.getState().activeFileId).toBe(workspaceFileId(project));
  });
  it("moves unsaved drafts with renamed directories without affecting another root", () => {
    const workbench = useWorkbenchStore.getState();
    workbench.openFile(workspace.path, workspace.root);
    workbench.openFile(project.path, project.root);
    workbench.activateFile(workspaceFileId(workspace));
    useFileEditorStore.setState({ tabs: new Map([
      [workspaceFileId(workspace), { file, draft: "unsaved workspace" }],
      [workspaceFileId(project), { file, draft: "unsaved project" }],
    ]) });
    workbench.remapOpenFile("docs", "notes", "workspace");
    const renamed = { ...workspace, path: "notes/note.md" };
    expect(useWorkbenchStore.getState().activeFileId).toBe(workspaceFileId(renamed));
    expect(useFileEditorStore.getState().tabs.get(workspaceFileId(renamed))).toMatchObject({
      draft: "unsaved workspace", file: { path: "notes/note.md" },
    });
    expect(useFileEditorStore.getState().tabs.get(workspaceFileId(project))?.draft).toBe("unsaved project");
    workbench.closeFilesUnder("notes", "workspace");
    expect(useWorkbenchStore.getState().openFiles).toEqual([project]);
  });
});
