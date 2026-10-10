import { useTranslation } from "react-i18next";
import { lazy, Suspense } from "react";
import { Activity, FolderTree, ListTodo, Loader2, ScanText, Search, Settings, X } from "lucide-react";
import { useWorkbenchStore, type DockTab } from "@/stores/workbench-store";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { DialogSurface } from "@/components/ui/DialogSurface";

// 文件树依赖 react-arborist（react-dnd/react-window 体积较大），按需加载
const FileTreePanel = lazy(() =>
  import("./filetree/FileTreePanel").then((m) => ({ default: m.FileTreePanel })),
);

const PANELS = {
  status: lazy(() => import("./dock/StatusPanel").then((m) => ({ default: m.StatusPanel }))),
  context: lazy(() => import("./dock/ContextPanel").then((m) => ({ default: m.ContextPanel }))),
  tasks: lazy(() => import("./dock/TasksPanel").then((m) => ({ default: m.DockTasksPanel }))),
  search: lazy(() => import("./dock/SearchPanel").then((m) => ({ default: m.SearchPanel }))),
  settings: lazy(() => import("./dock/SettingsPanel").then((m) => ({ default: m.SettingsPanel }))),
};

/** 右侧功能 Dock：TabBar 切换状态/上下文/任务/搜索/设置（窄屏为抽屉） */
export function Dock({ overlay = false }: { overlay?: boolean }) {
  const { t } = useTranslation("workbench");
  const activeTab = useWorkbenchStore((s) => s.activeTab);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const setActiveTab = useWorkbenchStore((s) => s.setActiveTab);
  const toggleDock = useWorkbenchStore((s) => overlay ? s.dismissSurface : s.toggleDock);

  const tabs: TabItem<DockTab>[] = [
    { key: "status", label: t("tabs.status"), icon: Activity },
    { key: "context", label: t("context:title"), icon: ScanText },
    { key: "tasks", label: t("tabs.tasks"), icon: ListTodo },
    { key: "search", label: t("tabs.search"), icon: Search },
    { key: "settings", label: t("tabs.settings"), icon: Settings },
  ];

  if (!dockOpen) return null;

  const ActivePanel = PANELS[activeTab];

  const body = (
    <div className="workbench-panel flex flex-col h-full min-w-0 bg-panel">
      <div className="flex items-center shrink-0 gap-2 px-4 py-3 border-b border-border">
        <span className="flex-1 text-sm font-semibold text-heading">{t("toggleDock")}</span>
        <button aria-label={t("common:close")} onClick={toggleDock} className="panel-close"><X size={17} /></button>
      </div>
      <div className="px-3 pt-3 shrink-0">
        <div className="flex-1 min-w-0">
          <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} fill iconOnly />
        </div>
      </div>
      <div className="shrink-0 border-b border-border px-3 py-2 text-xs font-semibold text-heading">{tabs.find((tab) => tab.key === activeTab)?.label}</div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <Suspense fallback={<div role="status" className="p-4 text-xs text-muted">{t("common:loading")}</div>}><ActivePanel /></Suspense>
      </div>
    </div>
  );

  if (overlay) {
    return (
      <DialogSurface open title={t("toggleDock")} onClose={toggleDock} placement="right" className="workbench-sheet sm:max-w-[400px] border-0">
        {body}
      </DialogSurface>
    );
  }
  return body;
}

/** 左侧文件树栏（窄屏为抽屉） */
export function LeftDock({ overlay = false }: { overlay?: boolean }) {
  const { t } = useTranslation("workbench");
  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const toggleLeft = useWorkbenchStore((s) => overlay ? s.dismissSurface : s.toggleLeft);

  if (!leftOpen) return null;

  const body = (
    <div className="workbench-panel flex flex-col h-full w-full min-w-0 bg-panel">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border shrink-0">
        <FolderTree size={16} className="text-accent" /><span className="flex-1 text-sm font-semibold text-heading">{t("toggleFiles")}</span>
        <button aria-label={t("common:close")} onClick={toggleLeft} className="panel-close"><X size={17} /></button>
      </div>
      <Suspense
        fallback={
          <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted">
            <Loader2 size={13} className="animate-spin" />
          </div>
        }
      >
        <FileTreePanel />
      </Suspense>
    </div>
  );

  if (overlay) {
    return (
      <DialogSurface open title={t("toggleFiles")} onClose={toggleLeft} placement="left" className="workbench-sheet sm:max-w-[360px] border-0">
        {body}
      </DialogSurface>
    );
  }
  return body;
}
