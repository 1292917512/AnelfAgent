import { useTranslation } from "react-i18next";
import { ListX, Maximize2, Minimize2, PanelLeftClose, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TabState } from "./fileEditorUtils";

/** 文件编辑器标签栏：激活/关闭/全屏/收起 */
export function FileEditorTabs({
  openFiles,
  tabs,
  openFilePath,
  onActivate,
  onRequestClose,
  onRequestCloseAll,
  filePanelExpanded,
  onToggleExpanded,
  onCollapse,
}: {
  openFiles: string[];
  tabs: Map<string, TabState>;
  openFilePath: string | null;
  onActivate: (path: string) => void;
  onRequestClose: (path: string) => void;
  onRequestCloseAll: () => void;
  filePanelExpanded: boolean;
  onToggleExpanded: () => void;
  onCollapse: () => void;
}) {
  const { t } = useTranslation("workbench");
  return (
    <div className="flex items-center gap-1 pl-2 pr-1 py-1.5 border-b border-border shrink-0">
      <div className="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto">
        {openFiles.map((p) => {
          const tab = tabs.get(p);
          const tabDirty = tab ? tab.draft !== tab.file.content : false;
          const active = p === openFilePath;
          return (
            <span
              key={p}
              role="button"
              tabIndex={0}
              aria-selected={active}
              aria-label={p}
              onClick={() => onActivate(p)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onActivate(p); } }}
              onAuxClick={(e) => { if (e.button === 1) onRequestClose(p); }}
              title={p}
              className={cn(
                "flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-md text-xs cursor-pointer select-none shrink-0 max-w-[160px] transition-colors",
                active ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-foreground",
              )}
            >
              {tabDirty && <span className="w-1.5 h-1.5 rounded-full bg-warn shrink-0" aria-label="dirty" />}
              <span className="truncate">{p.split("/").pop() || p}</span>
              <button
                onClick={(e) => { e.stopPropagation(); onRequestClose(p); }}
                className="p-0.5 rounded hover:bg-hover shrink-0"
                aria-label="close tab"
              >
                <X size={11} />
              </button>
            </span>
          );
        })}
      </div>
      <button
        onClick={onRequestCloseAll}
        title={t("editor.closeAll")}
        className="p-1 rounded text-muted hover:text-foreground hover:bg-hover shrink-0 transition-colors"
      >
        <ListX size={14} />
      </button>
      <button
        onClick={onToggleExpanded}
        title={filePanelExpanded ? t("editor.exitFullscreen") : t("editor.fullscreen")}
        className={cn(
          "p-1 rounded shrink-0 transition-colors",
          filePanelExpanded ? "text-accent bg-accent-subtle" : "text-muted hover:text-foreground hover:bg-hover",
        )}
      >
        {filePanelExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
      </button>
      <button
        onClick={onCollapse}
        title={t("editor.collapse")}
        className="p-1 rounded text-muted hover:text-foreground hover:bg-hover shrink-0 transition-colors"
      >
        <PanelLeftClose size={14} />
      </button>
    </div>
  );
}
