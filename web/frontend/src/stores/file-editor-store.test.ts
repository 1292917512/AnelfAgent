import { beforeEach, describe, expect, it } from "vitest";
import { useFileEditorStore } from "./file-editor-store";

describe("file editor sessions", () => {
  beforeEach(() => useFileEditorStore.setState({ tabs: new Map() }));
  it("retains newer input when an earlier save finishes", () => {
    const file = { path: "note.md", name: "note.md", content: "original", version: "v1", size: 8, modified: 0, binary: false, truncated: false };
    useFileEditorStore.getState().setTabs((tabs) => new Map(tabs).set(file.path, { file, draft: "newer input" }));
    useFileEditorStore.getState().acknowledge(file.path, { ...file, content: "submitted content", version: "v2" });
    expect(useFileEditorStore.getState().tabs.get(file.path)).toMatchObject({ draft: "newer input", file: { content: "submitted content" } });
  });
  it("keeps open drafts and never resurrects a closed file after saving", () => {
    const file = { path: "note.md", name: "note.md", content: "", version: "v1", size: 0, modified: 0, binary: false, truncated: false };
    useFileEditorStore.getState().setTabs((tabs) => new Map(tabs).set(file.path, { file, draft: "draft" }));
    useFileEditorStore.getState().prune([file.path]);
    expect(useFileEditorStore.getState().tabs.get(file.path)?.draft).toBe("draft");
    useFileEditorStore.getState().prune([]);
    useFileEditorStore.getState().acknowledge(file.path, { ...file, content: "late save", version: "v2" });
    expect(useFileEditorStore.getState().tabs.size).toBe(0);
  });
});
