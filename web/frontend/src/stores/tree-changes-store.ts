/** 树变更装饰 store — AI 文件改动在文件树上的可视化数据源。
 *
 * 与 changes-store 的分工：changes-store 为已打开编辑器提供刷新信号；
 * 本 store 面向「工作区当前状态」——turn 结束
 * 不消退，容量 FIFO 滚动销毁最旧条目。file_diff SSE 到达时写入，
 * FileTreeNode 渲染徽章/闪现，FileTree 据此触发已加载目录的局部刷新。
 */

import { create } from "zustand";
import type { ChatStreamingDiff } from "@/lib/types";

/** 变更条目容量：超出后按写入序逐出最旧（滚动销毁） */
const MAX_ENTRIES = 200;

export interface TreeChangeEntry {
  revision: number;
  additions: number;
  removals: number;
  /** rename 的源路径（A → B；局部刷新需同时覆盖旧父目录，否则旧文件滞留） */
  move_from?: string;
  /** 写入时刻（epoch 秒） */
  ts: number;
}

interface TreeChangesState {
  /** path → 最近一次改动（同路径多次改动覆盖合并） */
  entries: Record<string, TreeChangeEntry>;
  revision: number;
  record: (diff: ChatStreamingDiff) => void;
  clear: () => void;
}

export const useTreeChangesStore = create<TreeChangesState>((set) => ({
  entries: {},
  revision: 0,

  record: (diff) =>
    set((s) => {
      const prev = s.entries[diff.path];
      const entries = { ...s.entries };
      if (!prev && Object.keys(entries).length >= MAX_ENTRIES) {
        // FIFO 逐出最旧一条（Map 序近似插入序；同路径覆盖不挤容量）
        let oldestPath: string | null = null;
        let oldestRevision = Infinity;
        for (const [p, e] of Object.entries(entries)) {
          if (e.revision < oldestRevision) {
            oldestRevision = e.revision;
            oldestPath = p;
          }
        }
        if (oldestPath) delete entries[oldestPath];
      }
      entries[diff.path] = {
        revision: s.revision + 1,
        additions: (prev?.additions ?? 0) + diff.additions,
        removals: (prev?.removals ?? 0) + diff.removals,
        move_from: diff.move_from,
        ts: Date.now() / 1000,
      };
      return {
        entries,
        revision: s.revision + 1,
      };
    }),

  clear: () => set({ entries: {} }),
}));

/** 目录的子树变更计数（徽章聚合）：entries 键按前缀统计 */
export function dirChangeCount(entries: Record<string, TreeChangeEntry>, dirPath: string): number {
  const prefix = dirPath + "/";
  let n = 0;
  for (const p of Object.keys(entries)) {
    if (p.startsWith(prefix)) n += 1;
  }
  return n;
}

/** 是否有任何变更（过滤开关的可用性判断） */
export function hasTreeChanges(entries: Record<string, TreeChangeEntry>): boolean {
  return Object.keys(entries).length > 0;
}
