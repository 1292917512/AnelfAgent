/** 改动集 store — 按 turn 聚合 AI 的文件编辑 diff（含 turn 内时间线回放）。

 * 流式过程区只展示当前 turn 的 diff 闪现；本 store 把同一 turn 的 diff 累积成
 * 「本轮改动集」卡片沉淀进消息时间线（turn_end 时落为一条历史消息），并提供
 * 「点击单文件跳编辑器 / 编辑器联动刷新」的联动钩子。
 */

import { create } from "zustand";
import type { ChatStreamingDiff } from "@/lib/types";

export interface FileChangeEntry extends ChatStreamingDiff {
  /** 该 turn 内的序号（多次编辑同一文件叠加展示） */
  seq: number;
}

interface ChangesState {
  /** turnId → 该轮改动集（消息卡片的数据源） */
  byTurn: Record<string, FileChangeEntry[]>;
  /** 当前活跃 turnId（流式区末尾插入新 diff 的目标） */
  activeTurnId: string | null;
  /** 编辑器联动刷新信号（path → 版本号；FileEditor 订阅刷新已打开标签） */
  fileVersions: Record<string, number>;

  recordDiff: (turnId: string, diff: ChatStreamingDiff) => void;
  /** turn 收尾：返回该轮改动集（供固化成消息）并清空流式累积 */
  settleTurn: (turnId: string) => FileChangeEntry[];
  clearTurn: (turnId: string) => void;
  /** 编辑器联动：文件被 AI 修改后提升版本号，FileEditor 据此刷新 */
  bumpFileVersion: (path: string) => void;
}

export const useChangesStore = create<ChangesState>((set, get) => ({
  byTurn: {},
  activeTurnId: null,
  fileVersions: {},

  recordDiff: (turnId, diff) =>
    set((s) => {
      const list = s.byTurn[turnId] ?? [];
      const entry: FileChangeEntry = { ...diff, seq: list.length };
      return {
        byTurn: { ...s.byTurn, [turnId]: [...list, entry] },
        activeTurnId: turnId,
        fileVersions: {
          ...s.fileVersions,
          [diff.path]: (s.fileVersions[diff.path] ?? 0) + 1,
        },
      };
    }),

  settleTurn: (turnId) => {
    const list = get().byTurn[turnId] ?? [];
    set((s) => ({
      byTurn: Object.fromEntries(Object.entries(s.byTurn).filter(([k]) => k !== turnId)),
      activeTurnId: s.activeTurnId === turnId ? null : s.activeTurnId,
    }));
    return list;
  },

  clearTurn: (turnId) =>
    set((s) => ({
      byTurn: Object.fromEntries(Object.entries(s.byTurn).filter(([k]) => k !== turnId)),
      activeTurnId: s.activeTurnId === turnId ? null : s.activeTurnId,
    })),

  bumpFileVersion: (path) =>
    set((s) => ({
      fileVersions: { ...s.fileVersions, [path]: (s.fileVersions[path] ?? 0) + 1 },
    })),
}));
