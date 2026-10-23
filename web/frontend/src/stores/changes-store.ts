import { create } from "zustand";
import { workspaceFileId } from "@/lib/workspace-file";
import { useFileEditorStore } from "./file-editor-store";

const MAX_RECENT_FILES = 512;

interface ChangesState {
  fileVersions: Record<string, number>;
  revision: number;
  bumpFileVersion: (path: string) => void;
}

/** 文件版本信号供编辑器订阅；执行改动记录由全局过程保存。 */
export const useChangesStore = create<ChangesState>((set) => ({
  fileVersions: {},
  revision: 0,
  bumpFileVersion: (path) => set((state) => {
    const revision = state.revision + 1;
    const fileVersions = { ...state.fileVersions };
    delete fileVersions[path];
    fileVersions[path] = revision;
    const keys = Object.keys(fileVersions);
    let excess = keys.length - MAX_RECENT_FILES;
    const open = useFileEditorStore.getState().tabs;
    for (const key of keys) {
      if (excess <= 0) break;
      if (key === path || open.has(workspaceFileId({ path: key, root: "workspace" }))) continue;
      delete fileVersions[key];
      excess--;
    }
    return { fileVersions, revision };
  }),
}));
