import { useFileEditorStore } from "./file-editor-store";
import { isFileUnder, workspaceFileId, workspaceFileLabel, type WorkspaceFileRef } from "@/lib/workspace-file";
import { create } from "zustand";
import { uiApi } from "@/lib/api";
import type { WorkspaceRoot } from "@/lib/types";

export type DockTab = "status" | "trace" | "tasks" | "search" | "settings";
const DOCK_TABS: DockTab[] = ["status", "trace", "tasks", "search", "settings"];

export interface UiNotification {
  id: string;
  title: string;
  content: string;
  level: "info" | "success" | "warning" | "error";
  ts: number;
}

export interface UiAsk {
  ask_id: string;
  question: string;
  options: string[];
  ts: number;
}

const MAX_NOTIFICATIONS = 20;
const MAX_ASKS = 20;

export interface EditorSelectionRange {
  start_line: number;
  end_line: number;
}

export interface EditorSelection {
  /** 选区所在文件（工作区相对路径） */
  path: string;
  root: WorkspaceRoot;
  ranges: EditorSelectionRange[];
  /** 选区内容（后端注入时按预算截断） */
  content: string;
}

interface WorkbenchState {
  /** 左侧文件树栏 */
  leftOpen: boolean;
  /** 右侧 Dock 栏 */
  dockOpen: boolean;
  activeTab: DockTab;
  /** 编辑器已打开的工作区文件标签（保持打开顺序） */
  openFiles: WorkspaceFileRef[];
  activeFileId: string | null;
  /** 编辑器面板是否展开（收起时标签保留，再点文件即恢复） */
  filePanelOpen: boolean;
  /** 编辑器是否全屏展开（覆盖整个工作台，专注编辑/操作） */
  filePanelExpanded: boolean;
  /** 文件树定位路径（open_panel files 时展开） */
  fileTreeFocus: string | null;
  /** 编辑器当前选区（FileEditor 上报；无选区/面板收起时为 null） */
  selection: EditorSelection | null;
  /** 搜索面板预填关键词 */
  searchSeed: string;
  /** 注入输入框的草稿（consumeDraft 消费） */
  draft: string | null;
  draftSeq: number;
  notifications: UiNotification[];
  asks: UiAsk[];

  toggleLeft: () => void;
  toggleDock: () => void;
  setActiveTab: (tab: DockTab) => void;
  /** AI ui_open_panel 命令入口：打开面板并携带 payload */
  openPanel: (panel: string, payload?: string) => void;
  openFile: (path: string, root?: WorkspaceRoot) => void;
  activateFile: (id: string) => void;
  closeFile: (id?: string) => void;
  closeAllFiles: () => void;
  /** 文件树重命名/移动联动：oldPath 为文件则精确重映射，为目录则重映射其下全部已打开标签 */
  remapOpenFile: (oldPath: string, newPath: string, root: WorkspaceRoot) => void;
  /** 文件树删除联动：关闭该路径本身或其目录下全部已打开标签 */
  closeFilesUnder: (path: string, root: WorkspaceRoot) => void;
  /** 收起编辑器面板（保留全部标签与未保存草稿） */
  collapseFilePanel: () => void;
  /** 切换编辑器全屏展开 */
  toggleFilePanelExpanded: () => void;
  setFileTreeFocus: (path: string | null) => void;
  setSelection: (sel: EditorSelection | null) => void;
  setSearchSeed: (q: string) => void;
  setDraft: (text: string) => void;
  consumeDraft: () => string | null;
  pushNotification: (n: UiNotification) => void;
  dismissNotification: (id: string) => void;
  pushAsk: (a: UiAsk) => void;
  resolveAsk: (askId: string) => void;
}

export const useWorkbenchStore = create<WorkbenchState>((set, get) => ({
  leftOpen: false,
  dockOpen: false,
  activeTab: "status",
  openFiles: [],
  activeFileId: null,
  filePanelOpen: false,
  filePanelExpanded: false,
  fileTreeFocus: null,
  selection: null,
  searchSeed: "",
  draft: null,
  draftSeq: 0,
  notifications: [],
  asks: [],

  toggleLeft: () => set((s) => ({ leftOpen: !s.leftOpen })),
  toggleDock: () => set((s) => ({ dockOpen: !s.dockOpen })),
  setActiveTab: (tab) => set({ activeTab: tab, dockOpen: true }),

  openPanel: (panel, payload = "") => {
    // files 是左侧文件树栏而非右侧 Dock tab，单独处理
    if (panel === "files") {
      set({ leftOpen: true });
      if (payload) {
        get().openFile(payload);
        set({ fileTreeFocus: payload });
      }
      return;
    }
    const tab = DOCK_TABS.includes(panel as DockTab) ? (panel as DockTab) : "status";
    set({ activeTab: tab, dockOpen: true });
    if (tab === "search" && payload) set({ searchSeed: payload });
  },

  openFile: (path, root = "workspace") => set((state) => {
    const file = { path, root };
    const id = workspaceFileId(file);
    return {
      openFiles: state.openFiles.some((entry) => workspaceFileId(entry) === id) ? state.openFiles : [...state.openFiles, file],
      activeFileId: id, filePanelOpen: true, selection: null,
    };
  }),
  activateFile: (id) => set((state) => state.openFiles.some((file) => workspaceFileId(file) === id)
    ? { activeFileId: id, filePanelOpen: true, selection: null } : state),
  closeFile: (id) => set((state) => {
    const target = id ?? state.activeFileId;
    const index = state.openFiles.findIndex((file) => workspaceFileId(file) === target);
    if (index < 0) return state;
    const openFiles = state.openFiles.filter((file) => workspaceFileId(file) !== target);
    const adjacent = openFiles[Math.min(index, openFiles.length - 1)];
    return {
      openFiles,
      activeFileId: state.activeFileId === target ? (adjacent ? workspaceFileId(adjacent) : null) : state.activeFileId,
      filePanelOpen: !!openFiles.length && state.filePanelOpen,
      filePanelExpanded: !!openFiles.length && state.filePanelExpanded,
      selection: state.activeFileId === target ? null : state.selection,
    };
  }),
  closeAllFiles: () => set({ openFiles: [], activeFileId: null, filePanelOpen: false, filePanelExpanded: false, selection: null }),
  remapOpenFile: (oldPath, newPath, root) => set((state) => {
    const remaps = new Map<string, WorkspaceFileRef>();
    const openFiles = state.openFiles.map((file) => {
      if (!isFileUnder(file, oldPath, root)) return file;
      const renamed = { ...file, path: newPath + file.path.slice(oldPath.length) };
      remaps.set(workspaceFileId(file), renamed);
      return renamed;
    });
    if (!remaps.size) return state;
    useFileEditorStore.getState().remap(remaps);
    const active = state.activeFileId ? remaps.get(state.activeFileId) : undefined;
    return { openFiles, activeFileId: active ? workspaceFileId(active) : state.activeFileId, selection: null };
  }),
  closeFilesUnder: (path, root) => set((state) => {
    const openFiles = state.openFiles.filter((file) => !isFileUnder(file, path, root));
    if (openFiles.length === state.openFiles.length) return state;
    const active = openFiles.find((file) => workspaceFileId(file) === state.activeFileId) ?? openFiles[openFiles.length - 1];
    return {
      openFiles, activeFileId: active ? workspaceFileId(active) : null,
      filePanelOpen: !!openFiles.length && state.filePanelOpen,
      filePanelExpanded: !!openFiles.length && state.filePanelExpanded, selection: null,
    };
  }),
  collapseFilePanel: () => set({ filePanelOpen: false, selection: null }),
  toggleFilePanelExpanded: () => set((s) => ({ filePanelExpanded: !s.filePanelExpanded, filePanelOpen: true })),
  setFileTreeFocus: (path) => set({ fileTreeFocus: path }),
  setSelection: (sel) => set({ selection: sel }),
  setSearchSeed: (q) => set({ searchSeed: q }),

  setDraft: (text) => set((s) => ({ draft: text, draftSeq: s.draftSeq + 1 })),
  consumeDraft: () => {
    const d = get().draft;
    if (d !== null) set({ draft: null });
    return d;
  },

  pushNotification: (n) =>
    set((s) => ({ notifications: [n, ...s.notifications].slice(0, MAX_NOTIFICATIONS) })),
  dismissNotification: (id) =>
    set((s) => ({ notifications: s.notifications.filter((n) => n.id !== id) })),

  pushAsk: (a) => set((s) => ({ asks: [...s.asks.filter((x) => x.ask_id !== a.ask_id), a].slice(-MAX_ASKS) })),
  resolveAsk: (askId) => set((s) => ({ asks: s.asks.filter((a) => a.ask_id !== askId) })),
}));

// ── 状态上报（供 AI ui_get_state 查询） ─────────────────────────
let _reportTimer: ReturnType<typeof setTimeout> | null = null;

/** 订阅工作台状态变化，防抖上报后端 */
export function startUiStateReporting(): () => void {
  const unsub = useWorkbenchStore.subscribe(() => {
    if (_reportTimer) clearTimeout(_reportTimer);
    _reportTimer = setTimeout(() => {
      const state = useWorkbenchStore.getState();
      const active = state.openFiles.find((file) => workspaceFileId(file) === state.activeFileId);
      uiApi.reportState({
        active_tab: state.activeTab,
        dock_open: state.dockOpen,
        left_open: state.leftOpen,
        open_file: active ? workspaceFileLabel(active) : null,
        has_draft: state.draft !== null,
        pending_asks: state.asks.length,
        // 工作区上下文注入数据源（发送时渲染为消息前缀块）
        active_file: active ? workspaceFileLabel(active) : null,
        selection: state.selection ? { ...state.selection, path: workspaceFileLabel(state.selection) } : null,
        open_tabs: state.openFiles.map((file) => ({
          label: file.path.split("/").pop() ?? file.path,
          path: workspaceFileLabel(file),
        })),
      }).catch(() => { /* 上报失败忽略 */ });
    }, 800);
  });
  return () => {
    unsub();
    if (_reportTimer) clearTimeout(_reportTimer);
  };
}
