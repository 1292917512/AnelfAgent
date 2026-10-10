import { workspaceFileId, workspaceFileLabel, type WorkspaceFileRef } from "@/lib/workspace-file";
import { useTranslation } from "react-i18next";
import { ArrowLeft, FolderTree, ListX, Maximize2, Minimize2, PanelLeftClose, X } from "lucide-react";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { cn } from "@/lib/utils";
import type { TabState } from "@/stores/file-editor-store";

interface FileEditorTabsProps {
  overlay?: boolean;
  openFiles: WorkspaceFileRef[];
  tabs: Map<string, TabState>;
  activeFileId: string | null;
  onActivate: (id: string) => void;
  onRequestClose: (id: string) => void;
  onRequestCloseAll: () => void;
  filePanelExpanded: boolean;
  onToggleExpanded: () => void;
  onCollapse: () => void;
}

export function FileEditorTabs({ overlay = false, openFiles, tabs, activeFileId, onActivate, onRequestClose,
  onRequestCloseAll, filePanelExpanded, onToggleExpanded, onCollapse }: FileEditorTabsProps) {
  const { t } = useTranslation("workbench");
  const actionClass = "p-1 rounded text-muted hover:text-foreground hover:bg-hover shrink-0 transition-colors";
  return <><div className="editor-navigation flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
    {overlay && <button type="button" className="menu-action !w-auto !px-2" onClick={onCollapse}><ArrowLeft size={16} />{t("backToChat")}</button>}
    <span className="min-w-0 flex-1 truncate text-xs text-muted">{openFiles.find((file) => workspaceFileId(file) === activeFileId)?.path}</span>
    {overlay && <button type="button" className="panel-close" title={t("toggleFiles")} aria-label={t("toggleFiles")} onClick={() => useWorkbenchStore.getState().showSurface("files")}><FolderTree size={17} /></button>}
  </div><div className="editor-tabs flex items-center gap-1 pl-2 pr-1 py-1.5 border-b border-border shrink-0">
    <div className="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto">
      {openFiles.map((file) => {
        const id = workspaceFileId(file);
        const label = workspaceFileLabel(file);
        const tab = tabs.get(id);
        const dirty = tab && tab.draft !== tab.file.content;
        const active = id === activeFileId;
        return <div key={id} className={cn("flex items-center gap-1 rounded-md text-xs shrink-0 max-w-48",
          active ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-foreground")}
          onAuxClick={(event) => { if (event.button === 1) onRequestClose(id); }}>
          <button type="button" aria-pressed={active} aria-label={label} title={label}
            onClick={() => onActivate(id)} className="flex min-w-0 items-center gap-1 px-2 py-1.5">
            {dirty && <span className="w-1.5 h-1.5 rounded-full bg-warn shrink-0" aria-label={t("common:unsavedChanges")} />}
            <span className="truncate">{file.path.split("/").pop() || file.path}</span>
            {file.root === "project" && <span className="text-[9px] text-muted">{t("files.rootProject")}</span>}
          </button>
          <button type="button" onClick={() => onRequestClose(id)} className="mr-1 p-0.5 rounded hover:bg-hover shrink-0"
            aria-label={`${t("editor.close")} ${label}`}><X size={11} /></button>
        </div>;
      })}
    </div>
    <button onClick={onRequestCloseAll} title={t("editor.closeAll")} className={actionClass}><ListX size={14} /></button>
    {!overlay && <button onClick={onToggleExpanded} title={filePanelExpanded ? t("editor.exitFullscreen") : t("editor.fullscreen")}
      className={cn(actionClass, filePanelExpanded && "text-accent bg-accent-subtle")}>
      {filePanelExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
    </button>}
    <button onClick={onCollapse} title={t("editor.collapse")} className={actionClass}><PanelLeftClose size={14} /></button>
  </div></>;
}
