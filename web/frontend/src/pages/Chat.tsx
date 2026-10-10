import { useEffect, useState, lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { Group, Panel, Separator, type Layout } from "react-resizable-panels";
import { useElementSize } from "@/hooks/useElementSize";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { RealtimeCallProvider } from "./chat/RealtimeCallBar";
import { Dock, LeftDock } from "./chat/Dock";
import { WorkbenchToolbar } from "./chat/WorkbenchToolbar";
import { resolveWorkbenchLayout } from "@/lib/workbench-layout";

import { WebConversation } from "./chat/WebConversation";
import { DialogSurface } from "@/components/ui/DialogSurface";

const FileEditor = lazy(() => import("./chat/FileEditor").then((m) => ({ default: m.FileEditor })));
const ExecutionWorkspace = lazy(() => import("./chat/ExecutionWorkspace"));

const LAYOUT_KEY = "anelf:workbench-layout:";

function loadLayout(key: string): Layout | undefined {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(LAYOUT_KEY + key) ?? "null");
    if (!value || typeof value !== "object" || Array.isArray(value)) return;
    const entries = Object.entries(value);
    if (!entries.length) return;
    const layout: Layout = {};
    for (const [id, size] of entries) {
      if (typeof size !== "number" || !Number.isFinite(size) || size <= 0 || size > 100) return;
      layout[id] = size;
    }
    return layout;
  } catch { return; }
}

function ResizeHandle({ id }: { id: string }) {
  return <Separator id={id} className="workbench-separator" />;
}

/** 全局执行为主视图，Web 频道独立停靠，文件与辅助面板按可用空间排列。 */
export default function Chat() {
  const { t } = useTranslation("chat");
  const { ref, size } = useElementSize<HTMLDivElement>();
  const loadHistory = useChatStore((s) => s.loadHistory);
  const loadChats = useChatStore((s) => s.loadChats);
  const chatOpen = useWorkbenchStore((s) => s.chatOpen);
  const toggleChat = useWorkbenchStore((s) => s.toggleChat);
  const dismissSurface = useWorkbenchStore((s) => s.dismissSurface);
  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const hasOpenFiles = useWorkbenchStore((s) => s.openFiles.length > 0 && s.filePanelOpen);
  const expanded = useWorkbenchStore((s) => s.filePanelExpanded);
  const activeSurface = useWorkbenchStore((s) => s.activeSurface);
  const setContainerWidth = useWorkbenchStore((s) => s.setContainerWidth);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const width = size?.width ?? null;
  const layout = resolveWorkbenchLayout(width ?? 0, { files: leftOpen, editor: hasOpenFiles, dock: dockOpen, expanded, chat: chatOpen }, activeSurface);
  const { filesInline, editorInline, dockInline, chatInline, executionHidden } = layout;
  const layoutId = [filesInline && "files", editorInline && "editor", !executionHidden && "execution", chatInline && "chat", dockInline && "dock"].filter(Boolean).join("-");

  useEffect(() => { loadChats(); loadHistory(); }, [loadChats, loadHistory]);
  useEffect(() => {
    setContainerWidth(width);
    return () => setContainerWidth(null);
  }, [width, setContainerWidth]);

  const editor = (overlay: boolean) => <Suspense fallback={<div className="p-6 text-sm text-muted" role="status">{t("workbench:editor.loading")}</div>}><FileEditor overlay={overlay} /></Suspense>;

  return <RealtimeCallProvider>
    <div ref={ref} className="workbench flex h-full min-h-0 flex-col">
      <WorkbenchToolbar {...layout} onResetLayout={() => {
        try { localStorage.removeItem(LAYOUT_KEY + layoutId); } catch { /* 布局存储可选 */ }
        setLayoutRevision((revision) => revision + 1);
      }} />
      <Group key={`${layoutId}:${layoutRevision}`} orientation="horizontal" className="min-h-0 flex-1" defaultLayout={loadLayout(layoutId)}
        onLayoutChanged={(next, meta) => {
          if (meta.isUserInteraction) try { localStorage.setItem(LAYOUT_KEY + layoutId, JSON.stringify(next)); } catch { /* 布局存储可选 */ }
        }}>
        {filesInline && <><Panel id="files" defaultSize={256} minSize={240} maxSize="35%"><LeftDock /></Panel><ResizeHandle id="files-separator" /></>}
        {editorInline && <><Panel id="editor" defaultSize="45%" minSize={420}>{editor(false)}</Panel>{!executionHidden && <ResizeHandle id="editor-separator" />}</>}
        {!executionHidden && <Panel id="execution" minSize={Math.min(500, size?.width ?? 500)}><Suspense fallback={<div className="p-6 text-sm text-muted">{t("common:loading")}</div>}><ExecutionWorkspace /></Suspense></Panel>}
        {chatInline && <><ResizeHandle id="chat-separator" /><Panel id="chat" defaultSize={380} minSize={360} maxSize="45%"><WebConversation onClose={toggleChat} /></Panel></>}
        {dockInline && <><ResizeHandle id="dock-separator" /><Panel id="dock" defaultSize={340} minSize={320} maxSize="40%"><Dock /></Panel></>}
      </Group>
      <DialogSurface open={layout.chatVisible && !chatInline} onClose={dismissSurface} title={t("workbench:webChat")} placement="right" className="workbench-sheet sm:max-w-[520px]">
        <WebConversation onClose={dismissSurface} />
      </DialogSurface>
      {layout.filesVisible && !filesInline && <LeftDock overlay />}
      {layout.editorVisible && !editorInline && editor(true)}
      {layout.dockVisible && !dockInline && <Dock overlay />}
    </div>
  </RealtimeCallProvider>;
}
