import { useEffect, lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FolderTree, PanelRight, Trash2 } from "lucide-react";
import { chatApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore, startUiStateReporting } from "@/stores/workbench-store";
import { Button } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";
import { MessageList } from "./chat/MessageList";
import { ChatInput } from "./chat/ChatInput";
import { StatusBar } from "./chat/StatusBar";
import { Dock, LeftDock } from "./chat/Dock";
import { UiCommandHost } from "./chat/UiCommandHost";
import { ContextChip } from "./chat/ContextChip";

// CodeMirror 编辑器体积较大，仅在打开文件时按需加载
const FileEditor = lazy(() =>
  import("./chat/FileEditor").then((m) => ({ default: m.FileEditor })),
);

/** 对话工作台：左文件树 / 中对话流 / 右功能 Dock 三栏布局 */
export default function Chat() {
  const { t } = useTranslation("chat");
  const loadHistory = useChatStore((s) => s.loadHistory);
  const startSSE = useChatStore((s) => s.startSSE);
  const clearMessages = useChatStore((s) => s.clearMessages);

  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const toggleLeft = useWorkbenchStore((s) => s.toggleLeft);
  const toggleDock = useWorkbenchStore((s) => s.toggleDock);
  const hasOpenFiles = useWorkbenchStore((s) => s.openFiles.length > 0);

  const { data: botName } = useQuery({
    queryKey: ["botName"],
    queryFn: () => chatApi.botName().then((r) => r.data.name),
  });

  useEffect(() => {
    loadHistory();
    startSSE();
    const stopReporting = startUiStateReporting();
    return stopReporting;
  }, [loadHistory, startSSE]);

  return (
    <div className="relative flex h-full min-h-0 -m-3 md:-m-6">
      {/* 左栏：工作区文件树 */}
      <LeftDock />

      {/* 文件编辑器侧栏（非模态；收起面板不卸载，标签与草稿保留） */}
      {hasOpenFiles && (
        <Suspense fallback={null}>
          <FileEditor />
        </Suspense>
      )}

      {/* 中栏：对话流 */}
      <div className="flex-1 flex flex-col min-w-0 h-full p-3 md:p-4">
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

        <MessageList />
        <StatusBar />
        <ChatInput />
      </div>

      {/* 右栏：功能 Dock */}
      <Dock />

      {/* AI 界面命令宿主 */}
      <UiCommandHost />
    </div>
  );
}
