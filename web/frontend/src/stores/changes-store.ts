import { create } from "zustand";

interface ChangesState {
  fileVersions: Record<string, number>;
  bumpFileVersion: (path: string) => void;
}

/** 文件版本信号供编辑器订阅；执行改动记录由全局过程保存。 */
export const useChangesStore = create<ChangesState>((set) => ({
  fileVersions: {},
  bumpFileVersion: (path) => set((state) => ({
    fileVersions: { ...state.fileVersions, [path]: (state.fileVersions[path] ?? 0) + 1 },
  })),
}));
