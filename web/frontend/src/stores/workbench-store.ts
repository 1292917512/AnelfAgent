import { create } from "zustand";
import { uiApi, type WorkspaceRoot } from "@/lib/api";

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

interface WorkbenchState {
  /** 左侧文件树栏 */
  leftOpen: boolean;
  /** 右侧 Dock 栏 */
  dockOpen: boolean;
  activeTab: DockTab;
  /** 编辑器已打开的工作区文件标签（保持打开顺序） */
  openFiles: string[];
  /** 每个已打开文件所属根目录（workspace / project），缺省为 workspace */
  fileRoots: Record<string, WorkspaceRoot>;
  /** 当前激活的文件标签（openFiles 为空时为 null） */
  openFilePath: string | null;
  /** 编辑器面板是否展开（收起时标签保留，再点文件即恢复） */
  filePanelOpen: boolean;
  /** 编辑器是否全屏展开（覆盖整个工作台，专注编辑/操作） */
  filePanelExpanded: boolean;
  /** 文件树定位路径（open_panel files 时展开） */
  fileTreeFocus: string | null;
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
  /** 获取文件所属根目录（缺省 workspace） */
  fileRoot: (path: string) => WorkspaceRoot;
  activateFile: (path: string) => void;
  closeFile: (path?: string) => void;
  closeAllFiles: () => void;
  /** 收起编辑器面板（保留全部标签与未保存草稿） */
  collapseFilePanel: () => void;
  /** 切换编辑器全屏展开 */
  toggleFilePanelExpanded: () => void;
  setFileTreeFocus: (path: string | null) => void;
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
  dockOpen: true,
  activeTab: "status",
  openFiles: [],
  fileRoots: {},
  openFilePath: null,
  filePanelOpen: false,
  filePanelExpanded: false,
  fileTreeFocus: null,
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

  openFile: (path, root = "workspace") =>
    set((s) => ({
      openFiles: s.openFiles.includes(path) ? s.openFiles : [...s.openFiles, path],
      fileRoots: { ...s.fileRoots, [path]: root },
      openFilePath: path,
      filePanelOpen: true,
    })),
  fileRoot: (path) => get().fileRoots[path] ?? "workspace",
  activateFile: (path) =>
    set((s) => (s.openFiles.includes(path) ? { openFilePath: path, filePanelOpen: true } : {})),
  closeFile: (path) =>
    set((s) => {
      const target = path ?? s.openFilePath;
      if (!target) return {};
      const idx = s.openFiles.indexOf(target);
      const openFiles = s.openFiles.filter((p) => p !== target);
      const fileRoots = { ...s.fileRoots };
      delete fileRoots[target];
      // 关闭激活标签时切到相邻标签（优先右侧，否则左侧）
      const openFilePath =
        s.openFilePath === target ? (openFiles[Math.min(idx, openFiles.length - 1)] ?? null) : s.openFilePath;
      const empty = openFiles.length === 0;
      // 全部关闭时退出全屏、收起面板，回归默认布局
      return {
        openFiles,
        fileRoots,
        openFilePath,
        filePanelOpen: empty ? false : s.filePanelOpen,
        filePanelExpanded: empty ? false : s.filePanelExpanded,
      };
    }),
  closeAllFiles: () => set({ openFiles: [], fileRoots: {}, openFilePath: null, filePanelOpen: false, filePanelExpanded: false }),
  collapseFilePanel: () => set({ filePanelOpen: false }),
  toggleFilePanelExpanded: () => set((s) => ({ filePanelExpanded: !s.filePanelExpanded, filePanelOpen: true })),
  setFileTreeFocus: (path) => set({ fileTreeFocus: path }),
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
      uiApi.reportState({
        active_tab: state.activeTab,
        dock_open: state.dockOpen,
        left_open: state.leftOpen,
        open_file: state.openFilePath,
        has_draft: state.draft !== null,
        pending_asks: state.asks.length,
      }).catch(() => { /* 上报失败忽略 */ });
    }, 800);
  });
  return () => {
    unsub();
    if (_reportTimer) clearTimeout(_reportTimer);
  };
}
