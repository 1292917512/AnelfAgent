import { useEffect } from "react";
import type { WorkspaceRoot } from "@/lib/types";
import { useTreeChangesStore, type TreeChangeEntry } from "@/stores/tree-changes-store";
import { useFileTreeStore } from "./file-tree-store";
import { findNode, parentPath } from "./file-tree-utils";

const REFRESH_WINDOW_MS = 600;
const REFRESH_MAX_DIRS = 8;

/** 按固定窗口刷新已加载目录；持续事件不推迟刷新，关闭面板后释放队列与订阅。 */
export function useTreeChangeRefresh(root: WorkspaceRoot): void {
  useEffect(() => {
    if (root !== "workspace") return;
    let seen: Record<string, TreeChangeEntry> = {};
    const pending = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let flushing = false;
    let active = true;
    const loaded = (path: string) => {
      const children = useFileTreeStore.getState().trees[root].children;
      return children !== null && (path === "" || findNode(children, path)?.children !== undefined);
    };
    const schedule = () => {
      if (active && pending.size && !flushing && timer === undefined) timer = setTimeout(() => { void flush(); }, REFRESH_WINDOW_MS);
    };
    const flush = async () => {
      timer = undefined;
      flushing = true;
      const batch = [...pending].slice(0, REFRESH_MAX_DIRS);
      for (const path of batch) pending.delete(path);
      try {
        await Promise.all(batch.filter(loaded).map((path) => useFileTreeStore.getState().refreshDir(root, path)));
      } finally {
        flushing = false;
        schedule();
      }
    };
    const collect = (entries: Record<string, TreeChangeEntry>) => {
      for (const [path, entry] of Object.entries(entries)) {
        if (entry.revision === seen[path]?.revision) continue;
        const directories = [parentPath(path)];
        if (entry.move_from) directories.push(parentPath(entry.move_from));
        for (const directory of directories) if (loaded(directory)) pending.add(directory);
      }
      // Hold only the bounded current snapshot, including evictions and same-millisecond edits.
      seen = entries;
      schedule();
    };
    collect(useTreeChangesStore.getState().entries);
    const unsubscribe = useTreeChangesStore.subscribe((state) => collect(state.entries));
    return () => { active = false; clearTimeout(timer); pending.clear(); unsubscribe(); };
  }, [root]);
}
