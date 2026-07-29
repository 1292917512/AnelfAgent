import { useTranslation } from "react-i18next";
import { Check, Code, Columns2, Copy, Download, Eye, Paperclip, Quote } from "lucide-react";
import type { WorkspaceFile, WorkspaceFileKind } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui";
import type { ViewMode } from "./fileEditorUtils";

/** 文件编辑器操作工具条：视图切换 + 附件/引用/复制/下载 */
export function FileEditorToolbar({
  file,
  kind,
  viewMode,
  onViewModeChange,
  onAttach,
  onQuote,
  onCopy,
  copied,
  rawUrl,
}: {
  file: WorkspaceFile;
  kind: WorkspaceFileKind | null;
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  onAttach: () => void;
  onQuote: () => void;
  onCopy: () => void;
  copied: boolean;
  rawUrl: string;
}) {
  const { t } = useTranslation("workbench");
  return (
    <div className="flex items-center gap-1 px-2 py-1 border-b border-border shrink-0">
      {(kind === "markdown" || kind === "html" || kind === "csv") && !file.binary && !file.truncated && (
        <div className="flex items-center rounded-md border border-border overflow-hidden mr-1">
          {([
            { mode: "edit" as ViewMode, icon: Code, label: t("editor.editView") },
            { mode: "preview" as ViewMode, icon: Eye, label: t("editor.preview") },
            // csv 表格不提供分屏（源码即结构化数据，分屏收益低）
            ...(kind === "csv" ? [] : [{ mode: "split" as ViewMode, icon: Columns2, label: t("editor.split") }]),
          ]).map(({ mode, icon: Icon, label }) => (
            <button
              key={mode}
              onClick={() => onViewModeChange(mode)}
              title={label}
              className={cn(
                "flex items-center gap-1 px-2 py-1 text-xs transition-colors",
                viewMode === mode ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-foreground",
              )}
            >
              <Icon size={12} />
              <span className="hidden xl:inline">{label}</span>
            </button>
          ))}
        </div>
      )}
      <span className="flex-1" />
      <Button variant="ghost" size="icon" onClick={onAttach} title={t("editor.attach")}>
        <Paperclip size={14} />
      </Button>
      {!file.binary && !file.truncated && (
        <>
          <Button variant="ghost" size="icon" onClick={onQuote} title={t("editor.quote")}>
            <Quote size={14} />
          </Button>
          <Button variant="ghost" size="icon" onClick={onCopy} title={copied ? t("editor.copied") : t("editor.copy")}>
            {copied ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
          </Button>
        </>
      )}
      <a
        href={rawUrl}
        download={file.name}
        title={t("editor.download")}
        className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
      >
        <Download size={14} />
      </a>
    </div>
  );
}
