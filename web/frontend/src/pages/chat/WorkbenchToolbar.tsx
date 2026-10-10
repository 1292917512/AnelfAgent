import { FileCode2, FolderTree, MessageSquare, PanelRight, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { useWorkbenchStore } from "@/stores/workbench-store";

interface Props {
  filesInline: boolean;
  dockInline: boolean;
  conversationHidden: boolean;
  onResetLayout: () => void;
}

export function WorkbenchToolbar({ filesInline, dockInline, conversationHidden, onResetLayout }: Props) {
  const { t } = useTranslation("workbench");
  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const dockOpen = useWorkbenchStore((s) => s.dockOpen);
  const fileCount = useWorkbenchStore((s) => s.openFiles.length);
  const activeSurface = useWorkbenchStore((s) => s.activeSurface);
  const showSurface = useWorkbenchStore((s) => s.showSurface);
  const toggleLeft = useWorkbenchStore((s) => s.toggleLeft);
  const toggleDock = useWorkbenchStore((s) => s.toggleDock);
  const expandEditor = useWorkbenchStore((s) => s.toggleFilePanelExpanded);
  return <div className="workbench-toolbar">
    <div className="flex min-w-0 items-center gap-1">
      <Button variant="ghost" size="sm" aria-label={t("toggleFiles")} aria-pressed={filesInline || activeSurface === "files"}
        onClick={() => filesInline && leftOpen ? toggleLeft() : showSurface("files")}><FolderTree size={16} /><span>{t("files.title")}</span></Button>
      {fileCount > 0 && <Button variant="ghost" size="sm" aria-label={t("openEditor")} onClick={() => showSurface("editor")}><FileCode2 size={16} /><span>{t("editorLabel")}</span><span className="workbench-count">{fileCount}</span></Button>}
      {conversationHidden && <Button variant="ghost" size="sm" onClick={expandEditor}><MessageSquare size={16} />{t("backToChat")}</Button>}
    </div>
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="icon" className="hidden lg:inline-flex" onClick={onResetLayout} title={t("resetLayout")}><RotateCcw size={14} /></Button>
      <Button variant="ghost" size="sm" aria-label={t("toggleDock")} aria-pressed={dockInline || activeSurface === "dock"}
        onClick={() => dockInline && dockOpen ? toggleDock() : showSurface("dock")}><PanelRight size={16} /><span>{t("toggleDock")}</span></Button>
    </div>
  </div>;
}
