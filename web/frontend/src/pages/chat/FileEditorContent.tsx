import type { ReactNode } from "react";
import { useDeferredValue } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { isPreviewableBinary, workspaceMediaKind, type WorkspaceFileKind, type WorkspaceRoot } from "@/lib/api";
import { Lightbox } from "./render/Lightbox";
import { Markdown } from "./render/Markdown";
import { CsvPreview } from "./file_preview/CsvPreview";
import { DocxPreview } from "./file_preview/DocxPreview";
import { HtmlPreview } from "./file_preview/HtmlPreview";
import { PdfPreview } from "./file_preview/PdfPreview";
import { VideoPreview } from "./file_preview/VideoPreview";
import { XlsxPreview } from "./file_preview/XlsxPreview";
import type { TabState, ViewMode } from "./fileEditorUtils";

/** 文件编辑器内容区：加载态 + 二进制预览 + 文本编辑/渲染预览 */
export function FileEditorContent({
  cur,
  kind,
  mediaKind,
  rawUrl,
  curRoot,
  loading,
  loadError,
  viewMode,
  editorNode,
  lightboxOpen,
  onLightboxChange,
}: {
  cur: TabState | undefined;
  kind: WorkspaceFileKind | null;
  mediaKind: ReturnType<typeof workspaceMediaKind>;
  rawUrl: string;
  curRoot: WorkspaceRoot;
  loading: boolean;
  loadError: boolean;
  viewMode: ViewMode;
  editorNode: ReactNode;
  lightboxOpen: boolean;
  onLightboxChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("workbench");
  // 预览内容延迟渲染：击键优先响应编辑器，预览（Markdown/Shiki 高亮、HTML iframe）
  // 延迟到空闲帧更新，避免 split 模式下每次击键全量重解析/重建
  const deferredDraft = useDeferredValue(cur?.draft ?? "");
  return (
    <div className="flex-1 min-h-0 flex flex-col px-3 py-2">
      {loading && (
        <div className="flex items-center gap-2 py-8 justify-center text-sm text-muted">
          <Loader2 size={16} className="animate-spin" /> {t("editor.loading")}
        </div>
      )}
      {loadError && <p className="py-8 text-center text-sm text-danger">{t("editor.loadFailed")}</p>}

      {cur && cur.file.binary && mediaKind === "image" && (
        <div className="flex items-center justify-center py-4 overflow-y-auto">
          <img
            src={rawUrl}
            alt={cur.file.name}
            onClick={() => onLightboxChange(true)}
            className="max-w-full max-h-[70vh] rounded-md border border-border cursor-zoom-in hover:opacity-90 transition-opacity"
          />
          {lightboxOpen && <Lightbox src={rawUrl} alt={cur.file.name} onClose={() => onLightboxChange(false)} />}
        </div>
      )}
      {cur && cur.file.binary && mediaKind === "video" && (
        <VideoPreview path={cur.file.path} name={cur.file.name} root={curRoot} />
      )}
      {cur && cur.file.binary && mediaKind === "audio" && (
        <div className="flex items-center justify-center py-12">
          <audio controls src={rawUrl} className="w-full max-w-md" />
        </div>
      )}
      {cur && cur.file.binary && kind === "pdf" && (
        <div className="flex-1 min-h-0">
          <PdfPreview path={cur.file.path} title={cur.file.name} root={curRoot} />
        </div>
      )}
      {cur && cur.file.binary && kind === "docx" && (
        <div className="flex-1 min-h-0">
          <DocxPreview path={cur.file.path} title={cur.file.name} root={curRoot} />
        </div>
      )}
      {cur && cur.file.binary && kind === "xlsx" && (
        <div className="flex-1 min-h-0 flex flex-col">
          <XlsxPreview path={cur.file.path} title={cur.file.name} root={curRoot} />
        </div>
      )}
      {cur && cur.file.binary && !mediaKind && !isPreviewableBinary(cur.file.name) && (
        <p className="py-8 text-center text-sm text-muted">{t("editor.binaryFile")}</p>
      )}
      {cur && cur.file.truncated && (
        <p className="py-8 text-center text-sm text-muted">{t("editor.tooLarge")}</p>
      )}

      {cur && !cur.file.binary && !cur.file.truncated && (
        kind === "markdown" && viewMode === "preview" ? (
          <div className="flex-1 min-h-0 overflow-y-auto pr-1">
            <Markdown content={deferredDraft} />
          </div>
        ) : kind === "markdown" && viewMode === "split" ? (
          <div className="flex-1 min-h-0 grid grid-cols-2 gap-2">
            <div className="min-h-0 h-full">{editorNode}</div>
            <div className="min-h-0 h-full overflow-y-auto pr-1">
              <Markdown content={deferredDraft} />
            </div>
          </div>
        ) : kind === "html" && viewMode === "preview" ? (
          <div className="flex-1 min-h-0">
            <HtmlPreview html={deferredDraft} title={cur.file.name} />
          </div>
        ) : kind === "html" && viewMode === "split" ? (
          <div className="flex-1 min-h-0 grid grid-cols-2 gap-2">
            <div className="min-h-0 h-full">{editorNode}</div>
            <div className="min-h-0 h-full">
              <HtmlPreview html={deferredDraft} title={cur.file.name} />
            </div>
          </div>
        ) : kind === "csv" && viewMode === "preview" ? (
          <CsvPreview
            text={deferredDraft}
            delimiter={cur.file.name.toLowerCase().endsWith(".tsv") ? "\t" : ","}
          />
        ) : (
          <div className="flex-1 min-h-0">{editorNode}</div>
        )
      )}
    </div>
  );
}
