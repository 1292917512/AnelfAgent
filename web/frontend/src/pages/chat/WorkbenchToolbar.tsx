import { Activity, FileCode2, FolderTree, MessageSquare, PanelRight, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ContributionTools } from "@/components/extensions/ContributionTools";
import { useWorkbenchStore } from "@/stores/workbench-store";

interface Props {
  filesInline: boolean;
  dockInline: boolean;
  executionHidden: boolean;
  chatInline: boolean;
  chatVisible: boolean;
  onResetLayout: () => void;
}

export function WorkbenchToolbar({ filesInline, dockInline, executionHidden, chatInline, chatVisible, onResetLayout }: Props) {
  const { t } = useTranslation("workbench");
  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const fileCount = useWorkbenchStore((s) => s.openFiles.length);
  const activeSurface = useWorkbenchStore((s) => s.activeSurface);
  const showSurface = useWorkbenchStore((s) => s.showSurface);
  const toggleLeft = useWorkbenchStore((s) => s.toggleLeft);
  const toggleDock = useWorkbenchStore((s) => s.toggleDock);
  const showExecution = useWorkbenchStore((s) => s.showExecution);
  const toggleChat = useWorkbenchStore((s) => s.toggleChat);
  return <div className="workbench-toolbar">
    <div className="flex min-w-0 items-center gap-1">
      <Button variant="ghost" size="sm" aria-pressed={!executionHidden && activeSurface !== "chat"} onClick={showExecution}><Activity size={16} />{t("execution.title")}</Button>
      <Button variant="ghost" size="sm" aria-pressed={chatVisible} onClick={() => chatInline ? toggleChat() : showSurface("chat")}><MessageSquare size={16} />{t("webChat")}</Button>
      <Button variant="ghost" size="sm" aria-label={t("toggleFiles")} aria-pressed={filesInline || activeSurface === "files"}
        onClick={() => filesInline && leftOpen ? toggleLeft() : showSurface("files")}><FolderTree size={16} /><span className="hidden sm:inline">{t("files.title")}</span></Button>
      {fileCount > 0 && <Button variant="ghost" size="sm" aria-label={t("openEditor")} onClick={() => showSurface("editor")}><FileCode2 size={16} /><span className="hidden sm:inline">{t("editorLabel")}</span><span className="workbench-count">{fileCount}</span></Button>}
    </div>
    <div className="flex items-center gap-1">
      <ContributionTools slot="workspace.tools" />
      <Button variant="ghost" size="icon" className="hidden lg:inline-flex" onClick={onResetLayout} title={t("resetLayout")}><RotateCcw size={14} /></Button>
      <Button variant="ghost" size="sm" aria-label={t("toggleDock")} aria-pressed={dockInline || activeSurface === "dock"}
        onClick={() => dockInline && dockOpen ? toggleDock() : showSurface("dock")}><PanelRight size={16} /><span className="hidden sm:inline">{t("toggleDock")}</span></Button>
    </div>
  </div>;
}
