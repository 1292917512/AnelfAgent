import { create } from "zustand";
import { api } from "@/lib/api/client";
import type { ActivityRun, ActivitySnapshot } from "@/lib/types/activity";

function retainRuns(runs: ActivityRun[]): ActivityRun[] {
  const ordered = [...runs].sort((a, b) => a.started_at - b.started_at || a.id.localeCompare(b.id));
  const recent = new Set(ordered.filter((run) => run.status !== "running").slice(-30).map((run) => run.id));
  return ordered.filter((run) => run.status === "running" || recent.has(run.id));
}

interface ActivityState {
  epoch: string;
  runs: ActivityRun[];
  loaded: boolean;
  loading: boolean;
  error: unknown;
  load: () => Promise<void>;
  receive: (epoch: string, run: ActivityRun) => void;
  restore: (snapshot: ActivitySnapshot) => void;
}

/** 全局过程独立保存，快照与实时帧按修订归并，不修改任何 Web 会话。 */
export const useActivityStore = create<ActivityState>((set, get) => ({
  epoch: "", runs: [], loaded: false, loading: false, error: null,
  load: async () => {
    if (get().loading) return;
    const epoch = get().epoch;
    set({ loading: true, error: null });
    try {
      const { data } = await api.get<ActivitySnapshot>("/workspace/activity");
      if (get().epoch === epoch || get().epoch === data.epoch) get().restore(data);
    } catch (error) { set({ error }); }
    finally { set({ loading: false }); }
  },
  receive: (epoch, run) => set((state) => {
    const runs = state.epoch === epoch ? state.runs : [];
    const previous = runs.find((item) => item.id === run.id);
    if (previous && previous.revision >= run.revision) return state;
    return { epoch, runs: retainRuns([...runs.filter((item) => item.id !== run.id), run]) };
  }),
  restore: (snapshot) => set((state) => {
    const current = state.epoch === snapshot.epoch ? state.runs : [];
    const records = new Map(snapshot.runs.map((run) => [run.id, run]));
    for (const run of current) {
      if (run.revision > (records.get(run.id)?.revision ?? snapshot.revision)) records.set(run.id, run);
    }
    return { epoch: snapshot.epoch, runs: retainRuns([...records.values()]), loaded: true, error: null };
  }),
}));
