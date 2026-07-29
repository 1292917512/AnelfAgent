import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import CodeMirror from "@uiw/react-codemirror";
import {
  workspaceApi, workspaceFileKind, workspaceMediaKind,
  type WorkspaceFileKind,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useChatStore } from "@/stores/chat-store";
import { ConfirmDialog, toast } from "@/components/ui";
import { useIsMobile } from "@/lib/use-media-query";
import { FileEditorTabs } from "./FileEditorTabs";
import { FileEditorToolbar } from "./FileEditorToolbar";
import { FileEditorContent } from "./FileEditorContent";
import { FileEditorFooter } from "./FileEditorFooter";
import { defaultViewMode, langExtension, type TabState, type ViewMode } from "./fileEditorUtils";

/** 工作区文件编辑器：多标签侧栏（非模态）+ CodeMirror + Markdown 预览 + 对话操作 */
export function FileEditor() {
  const { t } = useTranslation("workbench");
  const theme = useAppStore((s) => s.theme);
  const isMobile = useIsMobile();
  const openFiles = useWorkbenchStore((s) => s.openFiles);
  const openFilePath = useWorkbenchStore((s) => s.openFilePath);
  const fileRoot = useWorkbenchStore((s) => s.fileRoot);
  const filePanelOpen = useWorkbenchStore((s) => s.filePanelOpen);
  const activateFile = useWorkbenchStore((s) => s.activateFile);
  const closeFile = useWorkbenchStore((s) => s.closeFile);
  const closeAllFiles = useWorkbenchStore((s) => s.closeAllFiles);
  const collapseFilePanel = useWorkbenchStore((s) => s.collapseFilePanel);
  const filePanelExpanded = useWorkbenchStore((s) => s.filePanelExpanded);
  const toggleFilePanelExpanded = useWorkbenchStore((s) => s.toggleFilePanelExpanded);
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
  // 当前文件所属根目录（workspace / project），决定读/写/预览的基准
  const curRoot = openFilePath ? fileRoot(openFilePath) : "workspace";
  // 富格式预览类型（markdown/html/csv/pdf/docx/xlsx），不命中为普通文本
  const kind: WorkspaceFileKind | null = openFilePath ? workspaceFileKind(openFilePath) : null;
  // 二进制媒体（图片/视频/音频）走预览而非文本编辑
  const mediaKind = cur?.file.binary ? workspaceMediaKind(cur.file.name) : null;
  const rawUrl = cur ? workspaceApi.rawUrl(cur.file.path, false, curRoot) : "";
  const dirty = cur !== undefined && cur.draft !== cur.file.content;

  // tabs 的 ref 镜像：加载 effect 只随激活路径触发，通过 ref 读取缓存避免重复请求
  const tabsRef = useRef(tabs);
  useEffect(() => {
    tabsRef.current = tabs;
  }, [tabs]);

  // 激活标签首次加载内容；切换标签按文件类型重置默认视图与加载状态
  useEffect(() => {
    setViewMode(openFilePath ? defaultViewMode(openFilePath) : "edit");
    setLoadError(false);
    setLoading(false);
    if (!openFilePath || tabsRef.current.has(openFilePath)) return;
    setLoading(true);
    workspaceApi.read(openFilePath, fileRoot(openFilePath)).then((r) => {
      setTabs((m) => new Map(m).set(openFilePath, { file: r.data, draft: r.data.content }));
    }).catch(() => {
      setLoadError(true);
    }).finally(() => setLoading(false));
  }, [openFilePath, fileRoot]);

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
      await workspaceApi.write(cur.file.path, cur.draft, curRoot);
      setTabs((m) => new Map(m).set(cur.file.path, { file: { ...cur.file, content: cur.draft }, draft: cur.draft }));
      setSavedTick(true);
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
      savedTimerRef.current = setTimeout(() => setSavedTick(false), 1500);
    } catch {
      toast.error(t("editor.saveFailed"));
    }
    finally { setSaving(false); }
  }, [cur, dirty, curRoot, t]);

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
    if (!filePanelExpanded || isMobile) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggleFilePanelExpanded();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [filePanelExpanded, isMobile, toggleFilePanelExpanded]);

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
        "flex flex-col h-full bg-panel border-border",
        isMobile
          ? "w-[90vw] max-w-lg border-l shadow-xl shrink-0"
          : filePanelExpanded
            ? "flex-1 min-w-0"
            : cn("shrink-0 border-r", splitWide ? "w-[40rem] xl:w-[48rem]" : "w-[24rem] xl:w-[28rem]"),
      )}
    >
      <FileEditorTabs
        openFiles={openFiles}
        tabs={tabs}
        openFilePath={openFilePath}
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

      <FileEditorContent
        cur={cur}
        kind={kind}
        mediaKind={mediaKind}
        rawUrl={rawUrl}
        curRoot={curRoot}
        loading={loading}
        loadError={loadError}
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
          savedTick={savedTick}
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
