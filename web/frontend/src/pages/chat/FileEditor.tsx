import { workspaceFileId } from "@/lib/workspace-file";
import { fileReferenceMarkdown } from "@/lib/file-reference";
import { useFileEditorStore } from "@/stores/file-editor-store";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import CodeMirror from "@uiw/react-codemirror";
import type { ViewUpdate } from "@codemirror/view";
import { workspaceApi } from "@/lib/api";
import type { WorkspaceFileKind } from "@/lib/types";
import { workspaceFileKind, workspaceMediaKind } from "@/lib/workspace-kind";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useChatStore } from "@/stores/chat-store";
import { useFileSession } from "./useFileSession";
import { FileConflictDialog } from "./FileConflictDialog";
import { QueryError } from "@/components/common/AsyncState";
import { ConfirmDialog, toast } from "@/components/ui";
import { DialogSurface } from "@/components/ui/DialogSurface";
import { useCompactWorkbench } from "@/lib/use-media-query";
import { FileEditorTabs } from "./FileEditorTabs";
import { FileEditorToolbar } from "./FileEditorToolbar";
import { FileEditorContent } from "./FileEditorContent";
import { FileEditorFooter } from "./FileEditorFooter";
import { defaultViewMode, langExtension, type ViewMode } from "./fileEditorUtils";

/** 工作区文件编辑器：多标签侧栏（非模态）+ CodeMirror + Markdown 预览 + 对话操作 */
export function FileEditor() {
  const { t } = useTranslation("workbench");
  const theme = useAppStore((s) => s.theme);
  const compact = useCompactWorkbench();
  const openFiles = useWorkbenchStore((s) => s.openFiles);
  const activeFileId = useWorkbenchStore((state) => state.activeFileId);
  const activeFile = openFiles.find((file) => workspaceFileId(file) === activeFileId);
  const openFilePath = activeFile?.path;
  const curRoot = activeFile?.root ?? "workspace";
  const filePanelOpen = useWorkbenchStore((s) => s.filePanelOpen);
  const activateFile = useWorkbenchStore((s) => s.activateFile);
  const closeFile = useWorkbenchStore((s) => s.closeFile);
  const closeAllFiles = useWorkbenchStore((s) => s.closeAllFiles);
  const collapseFilePanel = useWorkbenchStore((s) => s.collapseFilePanel);
  const filePanelExpanded = useWorkbenchStore((s) => s.filePanelExpanded);
  const toggleFilePanelExpanded = useWorkbenchStore((s) => s.toggleFilePanelExpanded);
  const setInputDraft = useWorkbenchStore((s) => s.setDraft);
  const attachWorkspaceFile = useChatStore((s) => s.attachWorkspaceFile);

  const tabs = useFileEditorStore((state) => state.tabs);
  const setTabs = useFileEditorStore((state) => state.setTabs);
  const session = useFileSession(activeFile);
  const { loading, saving, loadError, save } = session;
  const [copied, setCopied] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("edit");
  /** 待确认关闭的目标：path 为单标签，null path 语义为全部关闭 */
  const [confirmClose, setConfirmClose] = useState<{ path: string | null } | null>(null);

  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 组件卸载时清理定时器
  useEffect(() => () => {
    if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
  }, []);

  const cur = activeFileId ? tabs.get(activeFileId) : undefined;
  // 当前文件所属根目录（workspace / project），决定读/写/预览的基准
  // 富格式预览类型（markdown/html/csv/pdf/docx/xlsx），不命中为普通文本
  const kind: WorkspaceFileKind | null = openFilePath ? workspaceFileKind(openFilePath) : null;
  // 二进制媒体（图片/视频/音频）走预览而非文本编辑
  const mediaKind = cur?.file.binary ? workspaceMediaKind(cur.file.name) : null;
  const rawUrl = cur ? workspaceApi.rawUrl(cur.file.path, false, curRoot) : "";
  const dirty = cur !== undefined && cur.draft !== cur.file.content;

  useEffect(() => { setViewMode(openFilePath ? defaultViewMode(openFilePath) : "edit"); }, [openFilePath]);

  const updateDraft = useCallback((v: string) => {
    if (!activeFileId) return;
    setTabs((m) => {
      const tab = m.get(activeFileId);
      return tab ? new Map(m).set(activeFileId, { ...tab, draft: v }) : m;
    });
  }, [activeFileId, setTabs]);

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

  // 全屏展开时按 Esc 退出
  useEffect(() => {
    if (!filePanelExpanded || compact) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggleFilePanelExpanded();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [filePanelExpanded, compact, toggleFilePanelExpanded]);

  /** 关闭请求：含未保存修改时先弹确认 */
  const requestClose = useCallback((path?: string) => {
    const target = path ?? activeFileId;
    if (!target) return;
    const tab = tabs.get(target);
    if (tab && tab.draft !== tab.file.content) {
      setConfirmClose({ path: target });
    } else {
      closeFile(target);
    }
  }, [activeFileId, tabs, closeFile, setConfirmClose]);

  const requestCloseAll = useCallback(() => {
    const anyDirty = openFiles.some((p) => {
      const tab = tabs.get(workspaceFileId(p));
      return tab && tab.draft !== tab.file.content;
    });
    if (anyDirty) setConfirmClose({ path: null });
    else closeAllFiles();
  }, [openFiles, tabs, closeAllFiles, setConfirmClose]);

  /** 将文件作为附件挂到对话输入框 */
  const attachToChat = useCallback(() => {
    if (!cur) return;
    attachWorkspaceFile(cur.file.path, cur.file.name, curRoot);
    toast.success(t("editor.attach"));
  }, [cur, curRoot, attachWorkspaceFile, t]);

  /** 将选区或文件全文连同可点击路径引用填入对话草稿。 */
  const quoteToChat = useCallback(() => {
    if (!cur || cur.file.binary) return;
    const ext = cur.file.path.split(".").pop()?.toLowerCase() || "";
    const sel = useWorkbenchStore.getState().selection;
    const range = sel && sel.path === cur.file.path && sel.root === curRoot && sel.content ? (sel.ranges[0] ?? null) : null;
    const label = range ? `${cur.file.name}:L${range.start_line}-L${range.end_line}` : cur.file.name;
    const body = range && sel ? sel.content : cur.draft;
    const reference = fileReferenceMarkdown({ root: curRoot, path: cur.file.path, isDir: false }, label);
    const fence = "`".repeat(Array.from(body.matchAll(/`+/g)).reduce((length, match) => Math.max(length, match[0].length + 1), 3));
    setInputDraft(`${reference}\n${fence}${ext}\n${body}\n${fence}`);
    toast.success(t("editor.quote"));
  }, [cur, curRoot, setInputDraft, t]);

  const copyContent = useCallback(() => {
    if (!cur || cur.file.binary) return;
    navigator.clipboard.writeText(cur.draft).then(() => {
      setCopied(true);
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = setTimeout(() => setCopied(false), 1500);
    }).catch(() => { /* 剪贴板不可用时忽略 */ });
  }, [cur, setCopied]);

  /** 编辑器选区变化 → 写入工作台状态（ui_state 上报的数据源） */
  const reportSelection = useCallback((path: string, vu: ViewUpdate) => {
    const setSelection = useWorkbenchStore.getState().setSelection;
    const sel = vu.state.selection.main;
    if (sel.empty) {
      setSelection(null);
      return;
    }
    const doc = vu.state.doc;
    const startLine = doc.lineAt(sel.from).number;
    const endLine = doc.lineAt(sel.to).number;
    const content = vu.state.sliceDoc(sel.from, sel.to);
    setSelection({
      path,
      root: curRoot,
      ranges: [{ start_line: startLine, end_line: endLine }],
      content,
    });
  }, [curRoot]);

  // 面板收起时清空选区（关闭编辑器即无「当前选区」语义）
  useEffect(() => {
    if (!filePanelOpen) useWorkbenchStore.getState().setSelection(null);
  }, [filePanelOpen]);

  if (!filePanelOpen || !openFilePath) return null;

  const editorNode = cur && !cur.file.binary && !cur.file.truncated && (
    <CodeMirror
      key={activeFileId}
      value={cur.draft}
      onChange={updateDraft}
      onUpdate={(vu) => reportSelection(cur.file.path, vu)}
      extensions={langExtension(cur.file.path)}
      theme={theme}
      height="100%"
      style={{ height: "100%", fontSize: 13 }}
      basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
    />
  );

  const body = (
    <div
      className={cn(
        "flex flex-col h-full bg-panel border-border",
        compact
          ? "w-full shrink-0"
          : "w-full min-w-0 border-r",
      )}
    >
      <FileEditorTabs
        openFiles={openFiles}
        tabs={tabs}
        activeFileId={activeFileId}
        onActivate={activateFile}
        onRequestClose={requestClose}
        onRequestCloseAll={requestCloseAll}
        filePanelExpanded={filePanelExpanded}
        onToggleExpanded={toggleFilePanelExpanded}
        onCollapse={collapseFilePanel}
      />

      {cur && (
        <FileEditorToolbar
          file={cur.file}
          kind={kind}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          onAttach={attachToChat}
          onQuote={quoteToChat}
          onCopy={copyContent}
          copied={copied}
          rawUrl={rawUrl}
        />
      )}

      {session.saveError != null && <QueryError compact error={session.saveError} />}
      <FileConflictDialog session={session} />
      <FileEditorContent
        cur={cur}
        kind={kind}
        mediaKind={mediaKind}
        rawUrl={rawUrl}
        curRoot={curRoot}
        loading={loading}
        loadError={loadError}
        onRetry={session.retry}
        viewMode={viewMode}
        editorNode={editorNode}
        lightboxOpen={lightboxOpen}
        onLightboxChange={setLightboxOpen}
      />

      {cur && (
        <FileEditorFooter
          cur={cur}
          dirty={dirty}
          saving={saving}
          savedTick={session.saved && !dirty}
          onClose={() => requestClose()}
          onSave={save}
        />
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

  // 窄屏为全屏覆盖（编辑需要全宽；点遮罩/关闭仅收起面板，标签保留），宽屏参与布局流
  if (compact) {
    return (
      <DialogSurface open title={cur?.file.name ?? t("editor.loading")} onClose={collapseFilePanel}
        placement="right" className="border-0">
        {body}
      </DialogSurface>
    );
  }
  return body;
}
