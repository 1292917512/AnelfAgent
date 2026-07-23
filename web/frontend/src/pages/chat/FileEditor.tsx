import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import CodeMirror from "@uiw/react-codemirror";
import type { Extension } from "@codemirror/state";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import {
  Check, Code, Columns2, Copy, Download, Eye, ListX, Loader2, PanelLeftClose, Paperclip, Quote, Save, X,
} from "lucide-react";
import {
  isPreviewableBinary, workspaceApi, workspaceFileKind, workspaceMediaKind,
  type WorkspaceFile, type WorkspaceFileKind,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useChatStore } from "@/stores/chat-store";
import { Button, ConfirmDialog, toast } from "@/components/ui";
import { useIsMobile } from "@/lib/use-media-query";
import { Lightbox } from "./render/Lightbox";
import { Markdown } from "./render/Markdown";
import { CsvPreview } from "./file_preview/CsvPreview";
import { DocxPreview } from "./file_preview/DocxPreview";
import { HtmlPreview } from "./file_preview/HtmlPreview";
import { PdfPreview } from "./file_preview/PdfPreview";
import { VideoPreview } from "./file_preview/VideoPreview";
import { XlsxPreview } from "./file_preview/XlsxPreview";

/** 按扩展名映射 CodeMirror 语言包 */
function langExtension(path: string): Extension[] {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  switch (ext) {
    case "py": return [python()];
    case "js": case "jsx": case "mjs": case "cjs":
      return [javascript({ jsx: true })];
    case "ts": case "tsx":
      return [javascript({ jsx: true, typescript: true })];
    case "json": return [json()];
    case "md": case "markdown": return [markdown()];
    case "yaml": case "yml": return [yaml()];
    case "html": case "htm": case "xml": case "svg": return [html()];
    case "css": return [css()];
    default: return [];
  }
}

/** 单个标签页的编辑状态（file 为已保存内容，draft 为当前草稿） */
interface TabState {
  file: WorkspaceFile;
  draft: string;
}

type ViewMode = "edit" | "preview" | "split";

/** 文本类文件打开时的默认视图：可渲染格式（md/html/csv）默认预览，其余默认编辑 */
function defaultViewMode(path: string): ViewMode {
  const kind = workspaceFileKind(path);
  return kind === "markdown" || kind === "html" || kind === "csv" ? "preview" : "edit";
}

/** 工作区文件编辑器：多标签侧栏（非模态）+ CodeMirror + Markdown 预览 + 对话操作 */
export function FileEditor() {
  const { t } = useTranslation("workbench");
  const theme = useAppStore((s) => s.theme);
  const isMobile = useIsMobile();
  const openFiles = useWorkbenchStore((s) => s.openFiles);
  const openFilePath = useWorkbenchStore((s) => s.openFilePath);
  const filePanelOpen = useWorkbenchStore((s) => s.filePanelOpen);
  const activateFile = useWorkbenchStore((s) => s.activateFile);
  const closeFile = useWorkbenchStore((s) => s.closeFile);
  const closeAllFiles = useWorkbenchStore((s) => s.closeAllFiles);
  const collapseFilePanel = useWorkbenchStore((s) => s.collapseFilePanel);
  const setInputDraft = useWorkbenchStore((s) => s.setDraft);
  const attachWorkspaceFile = useChatStore((s) => s.attachWorkspaceFile);

  const [tabs, setTabs] = useState<Map<string, TabState>>(new Map());
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedTick, setSavedTick] = useState(false);
  const [copied, setCopied] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("edit");
  /** 待确认关闭的目标：path 为单标签，null path 语义为全部关闭 */
  const [confirmClose, setConfirmClose] = useState<{ path: string | null } | null>(null);

  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 组件卸载时清理定时器
  useEffect(() => () => {
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
  }, []);

  const cur = openFilePath ? tabs.get(openFilePath) : undefined;
  // 富格式预览类型（markdown/html/csv/pdf/docx/xlsx），不命中为普通文本
  const kind: WorkspaceFileKind | null = openFilePath ? workspaceFileKind(openFilePath) : null;
  // 二进制媒体（图片/视频/音频）走预览而非文本编辑
  const mediaKind = cur?.file.binary ? workspaceMediaKind(cur.file.name) : null;
  const rawUrl = cur ? workspaceApi.rawUrl(cur.file.path) : "";
  const dirty = cur !== undefined && cur.draft !== cur.file.content;

  // 激活标签首次加载内容；切换标签按文件类型重置默认视图与加载状态
  useEffect(() => {
    setViewMode(openFilePath ? defaultViewMode(openFilePath) : "edit");
    setLoadError(false);
    setLoading(false);
    if (!openFilePath || tabs.has(openFilePath)) return;
    setLoading(true);
    workspaceApi.read(openFilePath).then((r) => {
      setTabs((m) => new Map(m).set(openFilePath, { file: r.data, draft: r.data.content }));
    }).catch(() => {
      setLoadError(true);
    }).finally(() => setLoading(false));
    // 仅以激活路径为触发，tabs 由 setTabs 函数式更新访问
  }, [openFilePath]);

  // 清理已关闭标签的缓存（保留未关闭标签的未保存草稿）
  useEffect(() => {
    setTabs((m) => {
      if ([...m.keys()].every((k) => openFiles.includes(k))) return m;
      const next = new Map<string, TabState>();
      for (const p of openFiles) {
        const tab = m.get(p);
        if (tab) next.set(p, tab);
      }
      return next;
    });
  }, [openFiles]);

  const updateDraft = useCallback((v: string) => {
    if (!openFilePath) return;
    setTabs((m) => {
      const tab = m.get(openFilePath);
      return tab ? new Map(m).set(openFilePath, { ...tab, draft: v }) : m;
    });
  }, [openFilePath]);

  const save = useCallback(async () => {
    if (!cur || !dirty) return;
    setSaving(true);
    try {
      await workspaceApi.write(cur.file.path, cur.draft);
      setTabs((m) => new Map(m).set(cur.file.path, { file: { ...cur.file, content: cur.draft }, draft: cur.draft }));
      setSavedTick(true);
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
      savedTimerRef.current = setTimeout(() => setSavedTick(false), 1500);
    } catch {
      toast.error(t("editor.saveFailed"));
    }
    finally { setSaving(false); }
  }, [cur, dirty, t]);

  // Ctrl/Cmd+S 保存
  useEffect(() => {
    if (!openFilePath) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        save();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [openFilePath, save]);

  /** 关闭请求：含未保存修改时先弹确认 */
  const requestClose = useCallback((path?: string) => {
    const target = path ?? openFilePath;
    if (!target) return;
    const tab = tabs.get(target);
    if (tab && tab.draft !== tab.file.content) {
      setConfirmClose({ path: target });
    } else {
      closeFile(target);
    }
  }, [openFilePath, tabs, closeFile]);

  const requestCloseAll = useCallback(() => {
    const anyDirty = openFiles.some((p) => {
      const tab = tabs.get(p);
      return tab && tab.draft !== tab.file.content;
    });
    if (anyDirty) setConfirmClose({ path: null });
    else closeAllFiles();
  }, [openFiles, tabs, closeAllFiles]);

  /** 将文件作为附件挂到对话输入框 */
  const attachToChat = useCallback(() => {
    if (!cur) return;
    attachWorkspaceFile(cur.file.path, cur.file.name);
    toast.success(t("editor.attach"));
  }, [cur, attachWorkspaceFile, t]);

  /** 将文件内容以代码块形式引用到对话输入框 */
  const quoteToChat = useCallback(() => {
    if (!cur || cur.file.binary) return;
    const ext = cur.file.path.split(".").pop()?.toLowerCase() || "";
    setInputDraft("```" + ext + "\n" + cur.draft + "\n```");
    toast.success(t("editor.quote"));
  }, [cur, setInputDraft, t]);

  const copyContent = useCallback(() => {
    if (!cur || cur.file.binary) return;
    navigator.clipboard.writeText(cur.draft).then(() => {
      setCopied(true);
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = setTimeout(() => setCopied(false), 1500);
    }).catch(() => { /* 剪贴板不可用时忽略 */ });
  }, [cur]);

  // 面板收起时不渲染但保持挂载，标签缓存与未保存草稿不丢失
  if (!filePanelOpen || !openFilePath) return null;

  const editorNode = cur && !cur.file.binary && !cur.file.truncated && (
    <CodeMirror
      value={cur.draft}
      onChange={updateDraft}
      extensions={langExtension(cur.file.path)}
      theme={theme}
      height="100%"
      style={{ height: "100%", fontSize: 13 }}
      basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
    />
  );

  const splitWide = (kind === "markdown" || kind === "html") && viewMode === "split";

  const body = (
    <div
      className={cn(
        "flex flex-col h-full bg-panel border-border shrink-0",
        isMobile
          ? "w-[90vw] max-w-lg border-l shadow-xl"
          : cn("border-r", splitWide ? "w-[40rem] xl:w-[48rem]" : "w-[24rem] xl:w-[28rem]"),
      )}
    >
      {/* 标签栏 */}
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
                onClick={() => activateFile(p)}
                onKeyDown={(e) => { if (e.key === "Enter") activateFile(p); }}
                onAuxClick={(e) => { if (e.button === 1) requestClose(p); }}
                title={p}
                className={cn(
                  "flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-md text-xs cursor-pointer select-none shrink-0 max-w-[160px] transition-colors",
                  active ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-foreground",
                )}
              >
                {tabDirty && <span className="w-1.5 h-1.5 rounded-full bg-warn shrink-0" aria-label="dirty" />}
                <span className="truncate">{p.split("/").pop() || p}</span>
                <button
                  onClick={(e) => { e.stopPropagation(); requestClose(p); }}
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
          onClick={requestCloseAll}
          title={t("editor.closeAll")}
          className="p-1 rounded text-muted hover:text-foreground hover:bg-hover shrink-0 transition-colors"
        >
          <ListX size={14} />
        </button>
        <button
          onClick={collapseFilePanel}
          title={t("editor.collapse")}
          className="p-1 rounded text-muted hover:text-foreground hover:bg-hover shrink-0 transition-colors"
        >
          <PanelLeftClose size={14} />
        </button>
      </div>

      {/* 操作工具条 */}
      {cur && (
        <div className="flex items-center gap-1 px-2 py-1 border-b border-border shrink-0">
          {(kind === "markdown" || kind === "html" || kind === "csv") && !cur.file.binary && !cur.file.truncated && (
            <div className="flex items-center rounded-md border border-border overflow-hidden mr-1">
              {([
                { mode: "edit" as ViewMode, icon: Code, label: t("editor.editView") },
                { mode: "preview" as ViewMode, icon: Eye, label: t("editor.preview") },
                // csv 表格不提供分屏（源码即结构化数据，分屏收益低）
                ...(kind === "csv" ? [] : [{ mode: "split" as ViewMode, icon: Columns2, label: t("editor.split") }]),
              ]).map(({ mode, icon: Icon, label }) => (
                <button
                  key={mode}
                  onClick={() => setViewMode(mode)}
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
          <Button variant="ghost" size="icon" onClick={attachToChat} title={t("editor.attach")}>
            <Paperclip size={14} />
          </Button>
          {!cur.file.binary && !cur.file.truncated && (
            <>
              <Button variant="ghost" size="icon" onClick={quoteToChat} title={t("editor.quote")}>
                <Quote size={14} />
              </Button>
              <Button variant="ghost" size="icon" onClick={copyContent} title={copied ? t("editor.copied") : t("editor.copy")}>
                {copied ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
              </Button>
            </>
          )}
          <a
            href={rawUrl}
            download={cur.file.name}
            title={t("editor.download")}
            className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
          >
            <Download size={14} />
          </a>
        </div>
      )}

      {/* 内容区 */}
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
              onClick={() => setLightboxOpen(true)}
              className="max-w-full max-h-[70vh] rounded-md border border-border cursor-zoom-in hover:opacity-90 transition-opacity"
            />
            {lightboxOpen && <Lightbox src={rawUrl} alt={cur.file.name} onClose={() => setLightboxOpen(false)} />}
          </div>
        )}
        {cur && cur.file.binary && mediaKind === "video" && (
          <VideoPreview path={cur.file.path} name={cur.file.name} />
        )}
        {cur && cur.file.binary && mediaKind === "audio" && (
          <div className="flex items-center justify-center py-12">
            <audio controls src={rawUrl} className="w-full max-w-md" />
          </div>
        )}
        {cur && cur.file.binary && kind === "pdf" && (
          <div className="flex-1 min-h-0">
            <PdfPreview path={cur.file.path} title={cur.file.name} />
          </div>
        )}
        {cur && cur.file.binary && kind === "docx" && (
          <div className="flex-1 min-h-0">
            <DocxPreview path={cur.file.path} title={cur.file.name} />
          </div>
        )}
        {cur && cur.file.binary && kind === "xlsx" && (
          <div className="flex-1 min-h-0 flex flex-col">
            <XlsxPreview path={cur.file.path} title={cur.file.name} />
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
              <Markdown content={cur.draft} />
            </div>
          ) : kind === "markdown" && viewMode === "split" ? (
            <div className="flex-1 min-h-0 grid grid-cols-2 gap-2">
              <div className="min-h-0 h-full">{editorNode}</div>
              <div className="min-h-0 h-full overflow-y-auto pr-1">
                <Markdown content={cur.draft} />
              </div>
            </div>
          ) : kind === "html" && viewMode === "preview" ? (
            <div className="flex-1 min-h-0">
              <HtmlPreview html={cur.draft} title={cur.file.name} />
            </div>
          ) : kind === "html" && viewMode === "split" ? (
            <div className="flex-1 min-h-0 grid grid-cols-2 gap-2">
              <div className="min-h-0 h-full">{editorNode}</div>
              <div className="min-h-0 h-full">
                <HtmlPreview html={cur.draft} title={cur.file.name} />
              </div>
            </div>
          ) : kind === "csv" && viewMode === "preview" ? (
            <CsvPreview
              text={cur.draft}
              delimiter={cur.file.name.toLowerCase().endsWith(".tsv") ? "\t" : ","}
            />
          ) : (
            <div className="flex-1 min-h-0">{editorNode}</div>
          )
        )}
      </div>

      {/* 底栏 */}
      {cur && (
        <div className="flex items-center gap-2 px-3 py-2 border-t border-border shrink-0">
          <span className="text-[11px] text-muted mr-auto truncate">
            {cur.file.path} · {(cur.file.size / 1024).toFixed(1)} KB
            {savedTick && <span className="text-ok ml-2">{t("editor.saved")}</span>}
          </span>
          <Button variant="secondary" size="sm" onClick={() => requestClose()}>
            {t("editor.close")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={save}
            disabled={!dirty || saving || cur.file.binary || cur.file.truncated}
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            {t("editor.save")}
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={confirmClose !== null}
        onClose={() => setConfirmClose(null)}
        onConfirm={() => {
          if (confirmClose?.path) closeFile(confirmClose.path);
          else closeAllFiles();
          setConfirmClose(null);
        }}
        title={t("editor.discardTitle")}
        message={t("editor.discardConfirm")}
        confirmText={t("editor.close")}
        cancelText={t("common:cancel")}
        danger
      />
    </div>
  );

  // 移动端为抽屉式覆盖，桌面端参与布局流（不遮挡文件树）；点遮罩仅收起面板
  if (isMobile) {
    return (
      <div className="fixed inset-0 z-40" role="dialog" aria-modal="true">
        <div className="absolute inset-0 bg-black/50" onClick={collapseFilePanel} />
        <div className="absolute inset-y-0 right-0">{body}</div>
      </div>
    );
  }
  return body;
}
