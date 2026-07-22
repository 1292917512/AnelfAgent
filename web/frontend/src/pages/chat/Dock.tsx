import { useTranslation } from "react-i18next";
import { Activity, FolderTree, ListTodo, Search, Settings, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkbenchStore, type DockTab } from "@/stores/workbench-store";
import { useIsMobile } from "@/lib/use-media-query";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { StatusPanel } from "./dock/StatusPanel";
import { TracePanel } from "./dock/TracePanel";
import { TasksPanel } from "./dock/TasksPanel";
import { SearchPanel } from "./dock/SearchPanel";
import { SettingsPanel } from "./dock/SettingsPanel";
import { FileTreePanel } from "./dock/FileTreePanel";

const PANELS: Record<DockTab, () => React.JSX.Element> = {
  status: StatusPanel,
  trace: TracePanel,
  tasks: TasksPanel,
  search: SearchPanel,
  settings: SettingsPanel,
};

/** 右侧功能 Dock：TabBar 切换状态/思维/任务/搜索/设置（移动端为抽屉） */
export function Dock() {
  const { t } = useTranslation("workbench");
  const isMobile = useIsMobile();
  const activeTab = useWorkbenchStore((s) => s.activeTab);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const setActiveTab = useWorkbenchStore((s) => s.setActiveTab);
  const toggleDock = useWorkbenchStore((s) => s.toggleDock);

  const tabs: TabItem<DockTab>[] = [
    { key: "status", label: t("tabs.status"), icon: Activity },
    { key: "trace", label: t("tabs.trace"), icon: FolderTree },
    { key: "tasks", label: t("tabs.tasks"), icon: ListTodo },
    { key: "search", label: t("tabs.search"), icon: Search },
    { key: "settings", label: t("tabs.settings"), icon: Settings },
  ];

  if (!dockOpen) return null;

  const ActivePanel = PANELS[activeTab];

  const body = (
    <div className={cn(
      "flex flex-col h-full bg-panel border-border",
      isMobile ? "w-[85vw] max-w-sm border-l shadow-xl" : "w-72 xl:w-80 border-l",
    )}>
      <div className="flex items-center shrink-0">
        <div className="flex-1 min-w-0">
          <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} fill />
        </div>
        {isMobile && (
          <button onClick={toggleDock} className="p-2 text-muted hover:text-foreground shrink-0">
            <X size={16} />
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <ActivePanel />
      </div>
    </div>
  );

  if (isMobile) {
    return (
      <div className="fixed inset-0 z-40" role="dialog" aria-modal="true">
        <div className="absolute inset-0 bg-black/50" onClick={toggleDock} />
        <div className="absolute inset-y-0 right-0">{body}</div>
      </div>
    );
  }
  return body;
}

/** 左侧文件树栏（移动端为抽屉） */
export function LeftDock() {
  const isMobile = useIsMobile();
  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const toggleLeft = useWorkbenchStore((s) => s.toggleLeft);

  if (!leftOpen) return null;

  const body = (
    <div className={cn(
      "flex flex-col h-full bg-panel border-border",
      isMobile ? "w-[80vw] max-w-xs border-r shadow-xl" : "w-60 border-r",
    )}>
      {isMobile && (
        <div className="flex justify-end p-1 border-b border-border shrink-0">
          <button onClick={toggleLeft} className="p-1.5 text-muted hover:text-foreground">
            <X size={15} />
          </button>
        </div>
      )}
      <FileTreePanel />
    </div>
  );

  if (isMobile) {
    return (
      <div className="fixed inset-0 z-40" role="dialog" aria-modal="true">
        <div className="absolute inset-0 bg-black/50" onClick={toggleLeft} />
        <div className="absolute inset-y-0 left-0">{body}</div>
      </div>
    );
  }
  return body;
}
