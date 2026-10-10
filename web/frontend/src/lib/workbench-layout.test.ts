import { describe, expect, it } from "vitest";
import { resolveWorkbenchLayout } from "./workbench-layout";

describe("workspace space allocation", () => {
  it("reports only the selected overlay on narrow screens", () => {
    const panels = { files: true, editor: true, dock: true, expanded: false };
    expect(resolveWorkbenchLayout(390, panels, "editor")).toMatchObject({ filesVisible: false, editorVisible: true, dockVisible: false });
    expect(resolveWorkbenchLayout(390, panels)).toMatchObject({ filesVisible: false, editorVisible: false, dockVisible: false });
    expect(resolveWorkbenchLayout(1800, panels)).toMatchObject({ filesVisible: true, editorVisible: true, dockVisible: true });
  });
  it("keeps editing usable when navigation consumes the remaining room", () => {
    const panels = { files: true, editor: true, dock: true, expanded: false };
    expect(resolveWorkbenchLayout(1360, panels)).toMatchObject({ filesInline: true, editorInline: true, dockInline: false });
    expect(resolveWorkbenchLayout(1100, panels)).toMatchObject({ filesInline: false, editorInline: true, dockInline: false });
    expect(resolveWorkbenchLayout(820, panels)).toMatchObject({ filesInline: false, editorInline: false, dockInline: false, conversationHidden: false });
  });
  it("never hides the conversation for a fullscreen editor that must use an overlay", () => {
    expect(resolveWorkbenchLayout(390, { files: true, editor: true, dock: true, expanded: true })).toMatchObject({ conversationHidden: false });
    expect(resolveWorkbenchLayout(1600, { files: true, editor: true, dock: true, expanded: true })).toMatchObject({ conversationHidden: true, filesInline: true, dockInline: true });
  });
  it("reserves minimum readable widths across panel combinations", () => {
    for (const width of [360, 768, 960, 1100, 1280, 1428, 1432, 1440, 1920]) {
      for (let flags = 0; flags < 16; flags++) {
        const result = resolveWorkbenchLayout(width, { files: !!(flags & 1), editor: !!(flags & 2), dock: !!(flags & 4), expanded: !!(flags & 8) });
        const required = (result.conversationHidden ? 0 : Math.min(width, 440)) + (result.editorInline ? 420 : 0) + (result.editorInline && !result.conversationHidden ? 4 : 0) + (result.filesInline ? 244 : 0) + (result.dockInline ? 324 : 0);
        expect(required).toBeLessThanOrEqual(width);
      }
    }
  });
});
