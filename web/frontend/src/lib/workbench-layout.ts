export type WorkbenchSurface = "files" | "editor" | "dock" | "chat";

export interface WorkbenchPanels {
  files: boolean;
  editor: boolean;
  dock: boolean;
  expanded: boolean;
  chat: boolean;
  chatExpanded: boolean;
}

/** 以内容所需的最小宽度分配并排面板，剩余面板通过显式抽屉访问。 */
export function resolveWorkbenchLayout(width: number, panels: WorkbenchPanels, activeSurface: WorkbenchSurface | null = null) {
  if (panels.chat && panels.chatExpanded && width >= 960) return {
    executionVisible: false, executionHidden: true, filesInline: false, editorInline: false, dockInline: false,
    chatInline: true, chatVisible: true, filesVisible: false, editorVisible: false, dockVisible: false,
  };
  const editorInline = panels.editor && width >= 960;
  const executionHidden = editorInline && panels.expanded;
  let remaining = width - (executionHidden ? 0 : 500) - (editorInline ? 420 + (executionHidden ? 0 : 4) : 0);
  const filesInline = panels.files && width >= 960 && remaining >= 244;
  if (filesInline) remaining -= 244;
  const chatInline = panels.chat && !executionHidden && width >= 960 && remaining >= 364;
  if (chatInline) remaining -= 364;
  const dockInline = panels.dock && width >= 960 && remaining >= 324;
  const overlayActive = (activeSurface === "files" && panels.files && !filesInline)
    || (activeSurface === "editor" && panels.editor && !editorInline)
    || (activeSurface === "dock" && panels.dock && !dockInline)
    || (activeSurface === "chat" && panels.chat && !chatInline);
  return {
    executionVisible: !executionHidden && !overlayActive,
    filesInline, editorInline, dockInline, chatInline, executionHidden,
    chatVisible: panels.chat && (chatInline || activeSurface === "chat"),
    filesVisible: panels.files && (filesInline || activeSurface === "files"),
    editorVisible: panels.editor && (editorInline || activeSurface === "editor"),
    dockVisible: panels.dock && (dockInline || activeSurface === "dock"),
  };
}
