import type { ActivityRun } from "@/lib/types/activity";
import { hasActiveWork } from "@/lib/chat-activity";

export interface ActivityPage { id: string; runs: ActivityRun[]; startedAt: number; endedAt: number }

/** 同一父子调用树归为一轮，后台执行不吞并无关会话的历史。 */
export function activityFamilies(runs: ActivityRun[]): ActivityPage[] {
  const byId = new Map(runs.map((run) => [run.id, run]));
  const families = new Map<string, ActivityPage>();
  for (const run of runs) {
    let root = run;
    const seen = new Set([run.id]);
    while (root.parent_id && !seen.has(root.parent_id)) {
      const parent = byId.get(root.parent_id);
      if (!parent) break;
      seen.add(parent.id);
      root = parent;
    }
    let family = families.get(root.id);
    if (!family) {
      family = { id: root.id, runs: [], startedAt: root.started_at, endedAt: root.started_at };
      families.set(root.id, family);
    }
    family.runs.push(run);
    family.startedAt = Math.min(family.startedAt, run.started_at);
    family.endedAt = Math.max(family.endedAt, run.status === "running" ? Infinity : run.ended_at ?? run.updated_at);
  }
  const pages = [...families.values()].sort((a, b) => a.startedAt - b.startedAt || a.id.localeCompare(b.id));
  for (const page of pages) page.runs.sort((a, b) => a.started_at - b.started_at || a.id.localeCompare(b.id));
  return pages;
}

export function activityPages(runs: ActivityRun[]): ActivityPage[] {
  const families = activityFamilies(runs);
  if (!families.length) return [];
  const latest = families[families.length - 1]!;
  const current = families.filter((family) => family === latest || family.runs.some(hasActiveWork));
  return [...families.filter((family) => !current.includes(family)), {
    id: latest.id, runs: current.flatMap((family) => family.runs),
    startedAt: Math.min(...current.map((family) => family.startedAt)),
    endedAt: Math.max(...current.map((family) => family.endedAt)),
  }];
}
