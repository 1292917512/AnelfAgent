import type { ActivityRun } from "@/lib/types/activity";

export interface ActivityPage { id: string; runs: ActivityRun[]; startedAt: number; endedAt: number }

/** 同一调用树及时间重叠的并发执行归为一轮，历史按轮次向上翻阅。 */
export function activityPages(runs: ActivityRun[]): ActivityPage[] {
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
  const pages: ActivityPage[] = [];
  for (const family of [...families.values()].sort((a, b) => a.startedAt - b.startedAt)) {
    const previous = pages[pages.length - 1];
    if (previous && family.startedAt < previous.endedAt) {
      previous.runs.push(...family.runs);
      previous.endedAt = Math.max(previous.endedAt, family.endedAt);
    } else pages.push(family);
  }
  for (const page of pages) page.runs.sort((a, b) => a.started_at - b.started_at || a.id.localeCompare(b.id));
  return pages;
}
