import { uiApi } from "@/lib/api";
import { workspaceFileId, workspaceFileLabel } from "@/lib/workspace-file";
import { resolveWorkbenchLayout } from "@/lib/workbench-layout";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useChatStore } from "@/stores/chat-store";

function hasChatDraft(): boolean {
  const chat = useChatStore.getState();
  const bucket = chat.buckets[chat.activeChatId];
  return !!bucket && (!!bucket.inputDraft.trim() || bucket.pendingFiles.length > 0);
}

/** 上报当前可见面板和保留的编辑上下文，供 AI 查询工作台状态。 */
export function startUiStateReporting(page: string): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let hasDraft = hasChatDraft();
  const report = () => {
    const state = useWorkbenchStore.getState();
    const mounted = page === "/" && state.containerWidth !== null;
    const layout = resolveWorkbenchLayout(state.containerWidth ?? 0, {
      files: mounted && state.leftOpen,
      editor: mounted && state.filePanelOpen && state.openFiles.length > 0,
      dock: mounted && state.dockOpen,
      chat: mounted && state.chatOpen,
      expanded: state.filePanelExpanded,
      chatExpanded: state.chatExpanded,
    }, state.activeSurface);
    const active = state.openFiles.find((file) => workspaceFileId(file) === state.activeFileId);
    const activeFile = active ? workspaceFileLabel(active) : null;
    uiApi.reportState({
      page,
      chat_visible: layout.chatVisible,
      execution_visible: mounted && layout.executionVisible,
      active_tab: state.activeTab,
      dock_open: layout.dockVisible,
      left_open: layout.filesVisible,
      open_file: layout.editorVisible ? activeFile : null,
      has_draft: hasDraft || !!state.draft?.trim(),
      pending_asks: state.asks.length,
      active_file: activeFile,
      selection: layout.editorVisible && state.selection ? { ...state.selection, path: workspaceFileLabel(state.selection) } : null,
      open_tabs: state.openFiles.map((file) => ({ label: file.path.split("/").pop() ?? file.path, path: workspaceFileLabel(file) })),
    }).catch(() => { /* 状态上报不阻塞交互 */ });
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(report, 800);
  };
  report();
  const unsubscribeWorkbench = useWorkbenchStore.subscribe(schedule);
  const unsubscribeChat = useChatStore.subscribe(() => {
    const next = hasChatDraft();
    if (next !== hasDraft) { hasDraft = next; schedule(); }
  });
  return () => {
    unsubscribeWorkbench();
    unsubscribeChat();
    clearTimeout(timer);
  };
}
