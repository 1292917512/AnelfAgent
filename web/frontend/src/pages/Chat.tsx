import { useEffect, useState, lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FolderTree, PanelRight, Trash2 } from "lucide-react";
import { Group, Panel, Separator, type Layout, type LayoutChangedMeta } from "react-resizable-panels";
import { chatApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/lib/use-media-query";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore, startUiStateReporting } from "@/stores/workbench-store";
import { Button } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";
import { MessageList } from "./chat/MessageList";
import { ChatInput } from "./chat/ChatInput";
import { StatusBar } from "./chat/StatusBar";
import { ActivityBar } from "./chat/ActivityBar";
import { Dock, LeftDock } from "./chat/Dock";
import { UiCommandHost } from "./chat/UiCommandHost";
import { ContextChip } from "./chat/ContextChip";
import { ChatTabs } from "./chat/ChatTabs";
import { PlanPanel } from "@/components/plan/PlanPanel";

// CodeMirror 编辑器体积较大，仅在打开文件时按需加载
const FileEditor = lazy(() =>
  import("./chat/FileEditor").then((m) => ({ default: m.FileEditor })),
);

/** 三栏宽度持久化 key（用户拖拽后记住布局） */
const LAYOUT_STORAGE_KEY = "anelf:chat-layout-v1";

function loadLayout(): Layout | undefined {
  try {
    const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Layout) : undefined;
  } catch {
    return undefined;
  }
}

/** 栏间拖拽手柄（hover 高亮；双击复位到默认宽度，库内置行为） */
function ResizeHandle({ id }: { id: string }) {
  return (
    <Separator
      id={id}
      className="group relative w-1 shrink-0 bg-transparent hover:bg-accent/20 transition-colors"
    >
      <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border group-hover:bg-accent/50 transition-colors" />
    </Separator>
  );
}

/** 对话工作台：左文件树 / 中对话流 / 右功能 Dock 三栏布局（桌面端可拖拽调宽） */
export default function Chat() {
  const { t } = useTranslation("chat");
  const isMobile = useIsMobile();
  const loadHistory = useChatStore((s) => s.loadHistory);
  const startSSE = useChatStore((s) => s.startSSE);
  const loadChats = useChatStore((s) => s.loadChats);
  const clearMessages = useChatStore((s) => s.clearMessages);

  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const toggleLeft = useWorkbenchStore((s) => s.toggleLeft);
  const toggleDock = useWorkbenchStore((s) => s.toggleDock);
  const hasOpenFiles = useWorkbenchStore((s) => s.openFiles.length > 0);
  const filePanelExpanded = useWorkbenchStore((s) => s.filePanelExpanded);

  // 初始布局只读一次（后续拖拽经 onLayoutChanged 回写）
  const [defaultLayout] = useState<Layout | undefined>(loadLayout);

  const { data: botName } = useQuery({
    queryKey: ["botName"],
    queryFn: () => chatApi.botName().then((r) => r.data.name),
  });

  useEffect(() => {
    loadChats();
    loadHistory();
    startSSE();
    const stopReporting = startUiStateReporting();
    return stopReporting;
  }, [loadChats, loadHistory, startSSE]);

  const handleLayoutChanged = (layout: Layout, meta: LayoutChangedMeta) => {
    if (!meta.isUserInteraction) return;
    try {
      localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
    } catch { /* 存储不可用时忽略 */ }
  };

  // 中栏：对话流（编辑器全屏且有打开文件时让位隐藏，文件树/Dock 保留）
  const centerHidden = filePanelExpanded && hasOpenFiles;
  const center = (
    <div className="flex-1 flex flex-col min-w-0 h-full p-3 md:p-4 relative">
      {/* 头部 */}
      <div className="flex items-center justify-between gap-2 mb-3 shrink-0">
        <div className="flex items-center gap-1 min-w-0">
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleLeft}
            title={t("workbench:toggleFiles")}
            className={cn(leftOpen && "text-accent")}
          >
            <FolderTree size={16} />
          </Button>
          <h2 className="text-base md:text-lg font-semibold text-heading truncate">
            {botName ?? "Bot"}
          </h2>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <ContextChip />
          <ModelSelect modelType="chat" compact />
          <Button variant="secondary" size="sm" onClick={clearMessages}>
            <Trash2 size={14} />
            <span className="hidden sm:inline">{t("clear")}</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleDock}
            title={t("workbench:toggleDock")}
            className={cn(dockOpen && "text-accent")}
          >
            <PanelRight size={16} />
          </Button>
        </div>
      </div>

      {/* 多会话 Tab */}
      <ChatTabs />

      <MessageList />
      <StatusBar />
      <ActivityBar />
      <ChatInput />

      {/* 对话窗口内嵌入式悬浮计划窗（absolute，相对中栏容器定位，可拖拽） */}
      <PlanPanel />
    </div>
  );

  // 移动端：三栏全部退化为抽屉（Dock/LeftDock/FileEditor 内部自行处理）
  if (isMobile) {
    return (
      <div className="relative flex h-full min-h-0 -m-3 md:-m-6">
        <LeftDock />
        {hasOpenFiles && (
          <Suspense fallback={null}>
            <FileEditor />
          </Suspense>
        )}
        {!centerHidden && center}
        <Dock />
        <UiCommandHost />
      </div>
    );
  }

  // 桌面端：可拖拽调宽的三栏（宽度持久化到 localStorage，双击手柄复位）
  return (
    <div className="relative h-full min-h-0 -m-3 md:-m-6">
      <Group
        orientation="horizontal"
        className="h-full"
        defaultLayout={defaultLayout}
        onLayoutChanged={handleLayoutChanged}
      >
        {leftOpen && (
          <Panel id="left" defaultSize="15" minSize="10%" maxSize="30%" className="min-w-0 h-full">
            <LeftDock />
          </Panel>
        )}
        {leftOpen && <ResizeHandle id="sep-left" />}

        {hasOpenFiles && (
          <Panel
            id="editor"
            defaultSize={centerHidden ? "70" : "28"}
            minSize="18%"
            className="min-w-0 h-full"
          >
            <Suspense fallback={null}>
              <FileEditor />
            </Suspense>
          </Panel>
        )}
        {hasOpenFiles && !centerHidden && <ResizeHandle id="sep-editor" />}

        {!centerHidden && (
          <Panel id="center" minSize="25%" className="min-w-0 h-full">
            {center}
          </Panel>
        )}

        {dockOpen && !centerHidden && <ResizeHandle id="sep-dock" />}
        {dockOpen && (
          <Panel id="dock" defaultSize="20" minSize="14%" maxSize="40%" className="min-w-0 h-full">
            <Dock />
          </Panel>
        )}
      </Group>

      {/* AI 界面命令宿主 */}
      <UiCommandHost />
    </div>
  );
}
