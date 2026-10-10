import { describe, expect, it } from "vitest";
import { resolveWorkbenchLayout } from "./workbench-layout";

describe("workspace space allocation", () => {
  it("expands the conversation without discarding other panel preferences", () => {
    const panels = { files: true, editor: true, dock: true, expanded: false, chat: true, chatExpanded: true };
    expect(resolveWorkbenchLayout(1440, panels, "chat")).toMatchObject({ chatInline: true, chatVisible: true, executionHidden: true, editorVisible: false, filesVisible: false });
    expect(resolveWorkbenchLayout(390, panels, "chat")).toMatchObject({ chatInline: false, chatVisible: true, executionVisible: false });
    expect(resolveWorkbenchLayout(1440, { ...panels, chatExpanded: false })).toMatchObject({ editorVisible: true, filesVisible: true, executionVisible: true });
  });
  it("reports only the selected overlay on narrow screens", () => {
    const panels = { files: true, editor: true, dock: true, expanded: false, chat: true, chatExpanded: false };
    expect(resolveWorkbenchLayout(390, panels, "editor")).toMatchObject({ filesVisible: false, editorVisible: true, dockVisible: false });
    expect(resolveWorkbenchLayout(390, panels)).toMatchObject({ filesVisible: false, editorVisible: false, dockVisible: false });
    expect(resolveWorkbenchLayout(1920, panels)).toMatchObject({ filesVisible: true, editorVisible: true, dockVisible: true });
  });
  it("keeps editing usable when navigation consumes the remaining room", () => {
    const panels = { files: true, editor: true, dock: true, expanded: false, chat: true, chatExpanded: false };
    expect(resolveWorkbenchLayout(1360, panels)).toMatchObject({ filesInline: true, editorInline: true, dockInline: false });
    expect(resolveWorkbenchLayout(1100, panels)).toMatchObject({ filesInline: false, editorInline: true, dockInline: false });
    expect(resolveWorkbenchLayout(820, panels)).toMatchObject({ filesInline: false, editorInline: false, dockInline: false, executionHidden: false });
  });
  it("never hides the conversation for a fullscreen editor that must use an overlay", () => {
    expect(resolveWorkbenchLayout(390, { files: true, editor: true, dock: true, expanded: true, chat: true, chatExpanded: false })).toMatchObject({ executionHidden: false });
    expect(resolveWorkbenchLayout(1600, { files: true, editor: true, dock: true, expanded: true, chat: true, chatExpanded: false })).toMatchObject({ executionHidden: true, filesInline: true, dockInline: true });
  });
  it("reserves minimum readable widths across panel combinations", () => {
    for (const width of [360, 768, 960, 1100, 1280, 1428, 1432, 1440, 1920]) {
      for (let flags = 0; flags < 16; flags++) {
        const result = resolveWorkbenchLayout(width, { files: !!(flags & 1), editor: !!(flags & 2), dock: !!(flags & 4), expanded: !!(flags & 8), chat: true, chatExpanded: false });
        const required = (result.executionHidden ? 0 : Math.min(width, 500)) + (result.editorInline ? 420 : 0) + (result.editorInline && !result.executionHidden ? 4 : 0) + (result.filesInline ? 244 : 0) + (result.dockInline ? 324 : 0) + (result.chatInline ? 364 : 0);
        expect(required).toBeLessThanOrEqual(width);
      }
    }
  });
});
