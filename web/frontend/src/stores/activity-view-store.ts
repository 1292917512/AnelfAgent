import { create } from "zustand";
import type { ActivityViewState } from "@/lib/types/activity";

const emptyView: ActivityViewState = { selected: null, family: null, expanded: {} };

/** 仅保留当前进程的阅读标识与锚点，不复制活动记录或维持隐藏组件。 */
export const useActivityViewStore = create<{
  epoch: string;
  view: ActivityViewState;
  read: (epoch: string) => ActivityViewState;
  save: (epoch: string, view: ActivityViewState) => void;
}>((set, get) => ({
  epoch: "", view: emptyView,
  read: (epoch) => get().epoch === epoch ? get().view : emptyView,
  save: (epoch, view) => set({ epoch, view }),
}));
