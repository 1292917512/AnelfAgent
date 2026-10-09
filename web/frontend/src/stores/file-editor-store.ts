import { create } from "zustand";
import type { WorkspaceFile } from "@/lib/types";
import { workspaceFileId, type WorkspaceFileRef } from "@/lib/workspace-file";

export interface TabState { file: WorkspaceFile; draft: string }
interface FileEditorState {
  tabs: Map<string, TabState>;
  setTabs: (update: (tabs: Map<string, TabState>) => Map<string, TabState>) => void;
  prune: (paths: string[]) => void;
  acknowledge: (path: string, file: WorkspaceFile) => void;
  remap: (renamed: Map<string, WorkspaceFileRef>) => void;
}

export const useFileEditorStore = create<FileEditorState>((set) => ({
  tabs: new Map(),
  setTabs: (update) => set((state) => ({ tabs: update(state.tabs) })),
  prune: (paths) => set((state) => {
    const allowed = new Set(paths);
    if ([...state.tabs.keys()].every((path) => allowed.has(path))) return state;
    return { tabs: new Map([...state.tabs].filter(([path]) => allowed.has(path))) };
  }),
  acknowledge: (path, file) => set((state) => {
    const tab = state.tabs.get(path);
    if (!tab) return state;
    return { tabs: new Map(state.tabs).set(path, { ...tab, file }) };
  }),
  remap: (renamed) => set((state) => {
    const tabs = new Map(state.tabs);
    for (const [id, file] of renamed) {
      const tab = state.tabs.get(id);
      if (!tab) continue;
      tabs.delete(id);
      tabs.set(workspaceFileId(file), { ...tab, file: { ...tab.file, path: file.path, name: file.path.split("/").pop() ?? file.path } });
    }
    return { tabs };
  }),
}));
