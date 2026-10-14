import { create } from "zustand";
import { api } from "@/lib/api/client";
import type { ActivityRun, ActivitySnapshot } from "@/lib/types/activity";

function retainRuns(runs: ActivityRun[]): ActivityRun[] {
  const ordered = [...runs].sort((a, b) => a.started_at - b.started_at || a.id.localeCompare(b.id));
  while (ordered.length > 20) {
    const completed = ordered.findIndex((run) => run.status !== "running");
    ordered.splice(completed < 0 ? 0 : completed, 1);
  }
  return ordered;
}

interface ActivityState {
  epoch: string;
  snapshotRevision: number;
  runs: ActivityRun[];
  loaded: boolean;
  loading: boolean;
  clearing: boolean;
  error: unknown;
  load: () => Promise<void>;
  clearHistory: () => Promise<void>;
  receive: (epoch: string, run: ActivityRun) => void;
  restore: (snapshot: ActivitySnapshot) => void;
}

/** 全局过程独立保存，快照与实时帧按修订归并，不修改任何 Web 会话。 */
export const useActivityStore = create<ActivityState>((set, get) => {
  let pendingLoad: Promise<void> | null = null;
  return {
    epoch: "", snapshotRevision: 0, runs: [], loaded: false, loading: false, clearing: false, error: null,
    load: () => {
      if (pendingLoad) return pendingLoad;
      const epoch = get().epoch;
      set({ loading: true, error: null });
      pendingLoad = (async () => {
        try {
          const { data } = await api.get<ActivitySnapshot>("/workspace/activity");
          if (get().epoch === epoch || get().epoch === data.epoch) get().restore(data);
        } catch (error) { if (get().epoch === epoch) set({ error }); }
        finally { pendingLoad = null; set({ loading: false }); }
      })();
      return pendingLoad;
    },
    clearHistory: async () => {
      if (get().clearing) return;
      const epoch = get().epoch;
      set({ clearing: true, error: null });
      try {
        const { data } = await api.delete<ActivitySnapshot>("/workspace/activity/history");
        if (get().epoch === epoch || get().epoch === data.epoch) get().restore(data);
      } catch (error) { if (get().epoch === epoch) set({ error }); }
      finally { set({ clearing: false }); }
    },
    receive: (epoch, run) => set((state) => {
      const runs = state.epoch === epoch ? state.runs : [];
      const snapshotRevision = state.epoch === epoch ? state.snapshotRevision : 0;
      const previous = runs.find((item) => item.id === run.id);
      if (!previous && run.revision <= snapshotRevision) return state;
      if (previous && previous.revision >= run.revision) return state;
      return { epoch, snapshotRevision, runs: retainRuns([...runs.filter((item) => item.id !== run.id), run]) };
    }),
    restore: (snapshot) => set((state) => {
      if (state.epoch === snapshot.epoch && snapshot.revision < state.snapshotRevision) return state;
      const current = state.epoch === snapshot.epoch ? state.runs : [];
      const records = new Map(snapshot.runs.map((run) => [run.id, run]));
      for (const run of current) {
        const incoming = records.get(run.id);
        if (incoming ? run.revision >= incoming.revision : run.revision > snapshot.revision) records.set(run.id, run);
      }
      const ordered = retainRuns([...records.values()]);
      const runs = ordered.length === state.runs.length && ordered.every((run, index) => run === state.runs[index]) ? state.runs : ordered;
      if (state.epoch === snapshot.epoch && state.snapshotRevision === snapshot.revision && state.runs === runs && state.loaded && state.error == null) return state;
      return { epoch: snapshot.epoch, snapshotRevision: snapshot.revision, runs, loaded: true, error: null };
    }),
  };
});
