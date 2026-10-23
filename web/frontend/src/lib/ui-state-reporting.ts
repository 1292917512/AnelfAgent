import { uiApi } from "@/lib/api";
import { workspaceFileId, workspaceFileLabel } from "@/lib/workspace-file";
import { resolveWorkbenchLayout } from "@/lib/workbench-layout";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useChatStore } from "@/stores/chat-store";
import { nextCid } from "@/stores/chat-shared";

const clientId = nextCid();
let sequence = 0;
let lastActivity = performance.now();

function hasChatDraft(): boolean {
  const chat = useChatStore.getState();
  const bucket = chat.buckets[chat.activeChatId];
  return !!bucket && (!!bucket.inputDraft.trim() || bucket.pendingFiles.length > 0);
}

/** 上报当前可见面板和保留的编辑上下文，供 AI 查询工作台状态。 */
export function startUiStateReporting(page: string): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let hasDraft = hasChatDraft();
  let pageClosed = false;
  let streamStatus = useChatStore.getState().streamStatus;
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
    const snapshot = {
      client_id: clientId,
      sequence: ++sequence,
      connected: streamStatus === "connected",
      visible: document.visibilityState === "visible",
      focused: document.hasFocus(),
      idle_seconds: Math.max(0, (performance.now() - lastActivity) / 1000),
      closed: pageClosed,
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
    };
    if (pageClosed) {
      void fetch("/api/chat/ui-state", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: snapshot }), keepalive: true,
      }).catch(() => { /* 关闭上报失败由租约过期兜底。 */ });
    } else {
      void uiApi.reportState(snapshot).catch(() => { /* 状态上报不阻塞交互。 */ });
    }
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(report, 800);
  };
  const reportNow = () => { clearTimeout(timer); report(); };
  const onActivity = () => {
    const wasIdle = performance.now() - lastActivity >= 300_000;
    lastActivity = performance.now();
    if (wasIdle) reportNow();
  };
  const onPageHide = () => { pageClosed = true; reportNow(); };
  const onPageShow = () => { pageClosed = false; reportNow(); };
  report();
  const heartbeat = setInterval(reportNow, 15_000);
  document.addEventListener("visibilitychange", reportNow);
  window.addEventListener("focus", reportNow);
  window.addEventListener("blur", reportNow);
  window.addEventListener("pageshow", onPageShow);
  window.addEventListener("pagehide", onPageHide);
  document.addEventListener("pointerdown", onActivity, true);
  document.addEventListener("keydown", onActivity, true);
  document.addEventListener("pointermove", onActivity, { passive: true });
  const unsubscribeWorkbench = useWorkbenchStore.subscribe(schedule);
  const unsubscribeChat = useChatStore.subscribe(() => {
    const next = hasChatDraft();
    if (next !== hasDraft) { hasDraft = next; schedule(); }
    const nextStatus = useChatStore.getState().streamStatus;
    if (nextStatus !== streamStatus) { streamStatus = nextStatus; reportNow(); }
  });
  return () => {
    unsubscribeWorkbench();
    unsubscribeChat();
    clearTimeout(timer);
    clearInterval(heartbeat);
    document.removeEventListener("visibilitychange", reportNow);
    window.removeEventListener("focus", reportNow);
    window.removeEventListener("blur", reportNow);
    window.removeEventListener("pageshow", onPageShow);
    window.removeEventListener("pagehide", onPageHide);
    document.removeEventListener("pointerdown", onActivity, true);
    document.removeEventListener("keydown", onActivity, true);
    document.removeEventListener("pointermove", onActivity);
  };
}
