export type WorkbenchSurface = "files" | "editor" | "dock";

export interface WorkbenchPanels {
  files: boolean;
  editor: boolean;
  dock: boolean;
  expanded: boolean;
}

/** 以内容所需的最小宽度分配并排面板，剩余面板通过显式抽屉访问。 */
export function resolveWorkbenchLayout(width: number, panels: WorkbenchPanels, activeSurface: WorkbenchSurface | null = null) {
  const editorInline = panels.editor && width >= 960;
  const conversationHidden = editorInline && panels.expanded;
  let remaining = width - (conversationHidden ? 0 : 440) - (editorInline ? 420 + (conversationHidden ? 0 : 4) : 0);
  const filesInline = panels.files && width >= 960 && remaining >= 244;
  if (filesInline) remaining -= 244;
  const dockInline = panels.dock && width >= 960 && remaining >= 324;
  return {
    filesInline, editorInline, dockInline, conversationHidden,
    filesVisible: panels.files && (filesInline || activeSurface === "files"),
    editorVisible: panels.editor && (editorInline || activeSurface === "editor"),
    dockVisible: panels.dock && (dockInline || activeSurface === "dock"),
  };
}
