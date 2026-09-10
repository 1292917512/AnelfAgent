import { create } from "zustand";
import i18n from "@/i18n";
import { apiErrorMessage, workspaceApi, type WorkspaceNode, type WorkspaceRoot } from "@/lib/api";
import { toast } from "@/stores/toast-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { findNode, joinPath, parentPath, replaceChildren, uniqueName } from "./file-tree-utils";

interface RootTree {
  /** 根层节点；null = 尚未加载 */
  children: WorkspaceNode[] | null;
  truncated: boolean;
  loading: boolean;
  error: boolean;
}

const EMPTY_TREE: RootTree = { children: null, truncated: false, loading: false, error: false };

const dirKey = (root: WorkspaceRoot, path: string) => `${root}:${path}`;

const t = (key: string): string => i18n.t(key, { ns: "workbench" });

interface FileTreeState {
  trees: Record<WorkspaceRoot, RootTree>;
  /** 正在加载子级的目录（key = `${root}:${path}`） */
  loadingDirs: Record<string, boolean>;
  /** 当前选中节点路径（工具栏新建/上传的目标目录依据） */
  selection: Record<WorkspaceRoot, string | null>;
  /** 请求节点进入重命名编辑（FileTree 消费后清除） */
  pendingEdit: { root: WorkspaceRoot; path: string } | null;
  /** 全部折叠信号（序号递增触发） */
  collapseSeq: number;

  loadRoot: (root: WorkspaceRoot, force?: boolean) => Promise<void>;
  /** 加载目录子级（已加载则跳过；force 强制刷新） */
  loadChildren: (root: WorkspaceRoot, path: string, force?: boolean) => Promise<void>;
  /** 刷新目录（"" 为整棵树），展开状态由树组件按 id 保留 */
  refreshDir: (root: WorkspaceRoot, path: string) => Promise<void>;
  setSelection: (root: WorkspaceRoot, path: string | null) => void;
  /** 选中目录（选中文件时取其父目录），工具栏操作的目标 */
  selectedDir: (root: WorkspaceRoot) => string;
  requestEdit: (root: WorkspaceRoot, path: string) => void;
  clearPendingEdit: () => void;
  collapseAll: () => void;
  /** 新建文件/目录，返回新路径（失败 null）；names 为默认名（i18n 由调用方解析） */
  createEntry: (
    root: WorkspaceRoot,
    parent: string,
    kind: "file" | "folder",
    defaultName: string,
  ) => Promise<string | null>;
  renameEntry: (root: WorkspaceRoot, path: string, newName: string) => Promise<boolean>;
  moveEntries: (root: WorkspaceRoot, paths: string[], destDir: string) => Promise<boolean>;
  removeEntry: (root: WorkspaceRoot, path: string) => Promise<boolean>;
  uploadFiles: (root: WorkspaceRoot, dir: string, files: File[]) => Promise<boolean>;
}

export const useFileTreeStore = create<FileTreeState>((set, get) => ({
  trees: { workspace: EMPTY_TREE, project: EMPTY_TREE },
  loadingDirs: {},
  selection: { workspace: null, project: null },
  pendingEdit: null,
  collapseSeq: 0,

  loadRoot: async (root, force = false) => {
    const tree = get().trees[root];
    if (tree.loading || (tree.children !== null && !force)) return;
    set((s) => ({ trees: { ...s.trees, [root]: { ...s.trees[root], loading: true, error: false } } }));
    try {
      const res = await workspaceApi.tree("", 2, root);
      set((s) => ({
        trees: { ...s.trees, [root]: { children: res.data.children, truncated: res.data.truncated, loading: false, error: false } },
      }));
    } catch {
      set((s) => ({ trees: { ...s.trees, [root]: { ...s.trees[root], loading: false, error: true } } }));
    }
  },

  loadChildren: async (root, path, force = false) => {
    const key = dirKey(root, path);
    if (get().loadingDirs[key]) return;
    const node = findNode(get().trees[root].children ?? [], path);
    if (!force && node?.children !== undefined) return;
    set((s) => ({ loadingDirs: { ...s.loadingDirs, [key]: true } }));
    try {
      const res = await workspaceApi.tree(path, 1, root);
      set((s) => {
        const children = s.trees[root].children;
        if (children === null) return { loadingDirs: { ...s.loadingDirs, [key]: false } };
        return {
          loadingDirs: { ...s.loadingDirs, [key]: false },
          trees: {
            ...s.trees,
            [root]: {
              ...s.trees[root],
              children: replaceChildren(children, path, res.data.children),
              truncated: s.trees[root].truncated || res.data.truncated,
            },
          },
        };
      });
    } catch {
      set((s) => ({ loadingDirs: { ...s.loadingDirs, [key]: false } }));
      toast.error(t("files.loadFailed"));
    }
  },

  refreshDir: (root, path) =>
    path === "" ? get().loadRoot(root, true) : get().loadChildren(root, path, true),

  setSelection: (root, path) =>
    set((s) => ({ selection: { ...s.selection, [root]: path } })),

  selectedDir: (root) => {
    const sel = get().selection[root];
    if (!sel) return "";
    const node = findNode(get().trees[root].children ?? [], sel);
    return node?.type === "dir" ? node.path : parentPath(sel);
  },

  requestEdit: (root, path) => set({ pendingEdit: { root, path } }),
  clearPendingEdit: () => set({ pendingEdit: null }),
  collapseAll: () => set((s) => ({ collapseSeq: s.collapseSeq + 1 })),

  createEntry: async (root, parent, kind, defaultName) => {
    const children = get().trees[root].children ?? [];
    const siblings = parent === "" ? children : (findNode(children, parent)?.children ?? []);
    const path = joinPath(parent, uniqueName(siblings.map((n) => n.name), defaultName));
    try {
      if (kind === "file") await workspaceApi.write(path, "", root);
      else await workspaceApi.mkdir(path, root);
    } catch (e) {
      toast.error(apiErrorMessage(e, t("files.createFailed")));
      return null;
    }
    await get().refreshDir(root, parent);
    return path;
  },

  renameEntry: async (root, path, newName) => {
    const name = newName.trim();
    const node = findNode(get().trees[root].children ?? [], path);
    if (!node || !name || name === node.name || name.includes("/")) return false;
    const dst = joinPath(parentPath(path), name);
    try {
      const res = await workspaceApi.move(path, dst, root);
      useWorkbenchStore.getState().remapOpenFile(path, res.data.path);
      await get().refreshDir(root, parentPath(path));
      return true;
    } catch (e) {
      toast.error(apiErrorMessage(e, t("files.renameFailed")));
      await get().refreshDir(root, parentPath(path));
      return false;
    }
  },

  moveEntries: async (root, paths, destDir) => {
    const wb = useWorkbenchStore.getState();
    let moved = false;
    for (const src of paths) {
      const dst = joinPath(destDir, src.split("/").pop() ?? "");
      if (dst === src || dst.startsWith(src + "/")) continue;
      try {
        const res = await workspaceApi.move(src, dst, root);
        wb.remapOpenFile(src, res.data.path);
        moved = true;
        await get().refreshDir(root, parentPath(src));
      } catch (e) {
        toast.error(apiErrorMessage(e, t("files.moveFailed")));
      }
    }
    if (moved) await get().refreshDir(root, destDir);
    return moved;
  },

  removeEntry: async (root, path) => {
    try {
      await workspaceApi.remove(path, root);
    } catch (e) {
      toast.error(apiErrorMessage(e, t("files.deleteFailed")));
      return false;
    }
    useWorkbenchStore.getState().closeFilesUnder(path);
    await get().refreshDir(root, parentPath(path));
    return true;
  },

  uploadFiles: async (root, dir, files) => {
    let ok = true;
    for (const file of files) {
      try {
        await workspaceApi.upload(dir, file, root);
      } catch (e) {
        ok = false;
        toast.error(`${file.name}: ${apiErrorMessage(e, t("files.uploadFailed"))}`);
      }
    }
    await get().refreshDir(root, dir);
    return ok;
  },
}));
