import { useEffect, useState, lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Bot, Trash2 } from "lucide-react";
import { Group, Panel, Separator, type Layout } from "react-resizable-panels";
import { chatApi } from "@/lib/api";
import { useElementSize } from "@/hooks/useElementSize";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { Button } from "@/components/ui";
import { MessageList } from "./chat/MessageList";
import { ChatInput } from "./chat/ChatInput";
import { RealtimeCallProvider } from "./chat/RealtimeCallBar";
import { ChatDropZone } from "./chat/ChatDropZone";
import { StatusCapsule } from "./chat/StatusCapsule";
import { ActivityBar } from "./chat/ActivityBar";
import { Dock, LeftDock } from "./chat/Dock";
import { ChatTabs } from "./chat/ChatTabs";
import { PlanPanel } from "@/components/plan/PlanPanel";
import { WorkbenchToolbar } from "./chat/WorkbenchToolbar";
import { resolveWorkbenchLayout } from "@/lib/workbench-layout";

const FileEditor = lazy(() => import("./chat/FileEditor").then((m) => ({ default: m.FileEditor })));
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

/** 对话、文件和运行面板按容器空间排列；窄屏保留单一活动面板。 */
export default function Chat() {
  const { t } = useTranslation("chat");
  const { ref, size } = useElementSize<HTMLDivElement>();
  const loadHistory = useChatStore((s) => s.loadHistory);
  const loadChats = useChatStore((s) => s.loadChats);
  const clearMessages = useChatStore((s) => s.clearMessages);
  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const hasOpenFiles = useWorkbenchStore((s) => s.openFiles.length > 0 && s.filePanelOpen);
  const expanded = useWorkbenchStore((s) => s.filePanelExpanded);
  const activeSurface = useWorkbenchStore((s) => s.activeSurface);
  const setContainerWidth = useWorkbenchStore((s) => s.setContainerWidth);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const width = size?.width ?? null;
  const layout = resolveWorkbenchLayout(width ?? 0, { files: leftOpen, editor: hasOpenFiles, dock: dockOpen, expanded }, activeSurface);
  const { filesInline, editorInline, dockInline, conversationHidden } = layout;
  const layoutId = [filesInline && "files", editorInline && "editor", !conversationHidden && "chat", dockInline && "dock"].filter(Boolean).join("-");
  const { data: botName } = useQuery({ queryKey: ["botName"], queryFn: () => chatApi.botName().then((r) => r.data.name) });

  useEffect(() => { loadChats(); loadHistory(); }, [loadChats, loadHistory]);
  useEffect(() => {
    setContainerWidth(width);
    return () => setContainerWidth(null);
  }, [width, setContainerWidth]);

  const editor = (overlay: boolean) => <Suspense fallback={<div className="p-6 text-sm text-muted" role="status">{t("workbench:editor.loading")}</div>}><FileEditor overlay={overlay} /></Suspense>;
  const conversation = <ChatDropZone className="conversation-pane flex h-full min-w-0 flex-col">
    <div className="conversation-heading">
      <div className="conversation-identity flex min-w-0 items-center gap-2.5"><span className="agent-avatar"><Bot size={18} /></span><h2 className="truncate text-sm font-semibold text-heading">{botName ?? "AnelfAgent"}</h2></div>
      <ChatTabs />
      <Button variant="ghost" size="icon" title={t("clear")} onClick={clearMessages}><Trash2 size={15} /></Button>
    </div>
    <div className="conversation-body relative flex min-h-0 flex-1 flex-col">
      <MessageList /><ActivityBar /><StatusCapsule /><ChatInput /><PlanPanel />
    </div>
  </ChatDropZone>;

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
        {editorInline && <><Panel id="editor" defaultSize="45%" minSize={420}>{editor(false)}</Panel>{!conversationHidden && <ResizeHandle id="editor-separator" />}</>}
        {!conversationHidden && <Panel id="chat" minSize={Math.min(440, size?.width ?? 440)}>{conversation}</Panel>}
        {dockInline && <><ResizeHandle id="dock-separator" /><Panel id="dock" defaultSize={340} minSize={320} maxSize="40%"><Dock /></Panel></>}
      </Group>
      {layout.filesVisible && !filesInline && <LeftDock overlay />}
      {layout.editorVisible && !editorInline && editor(true)}
      {layout.dockVisible && !dockInline && <Dock overlay />}
    </div>
  </RealtimeCallProvider>;
}
