import type { ActivityEntry, ActivityRun } from "@/lib/types/activity";

export type ActivitySideView = "recollection" | "judgments" | "background" | "tasks" | "delegates";
export interface ActivitySideGroup { run: ActivityRun; entries: ActivityEntry[] }

/** 回忆与判断按所选会话保留记录，工作列表只保留运行项并合并其执行投影。 */
export function activitySideGroups(records: ActivityRun[], work: ActivityRun[]): Record<ActivitySideView, ActivitySideGroup[]> {
  const collect = (runs: ActivityRun[], matches: (entry: ActivityEntry) => boolean) => runs
    .map((run) => ({ run, entries: run.entries.filter(matches) })).filter(({ entries }) => entries.length > 0);
  const recollection = collect(records, (entry) => entry.kind === "context" || (entry.kind === "hook" && (entry.name.startsWith("memory.") || entry.name.startsWith("skills."))));
  const judgments = collect(records, (entry) => entry.kind === "hook" && !!entry.judgments?.length);
  const background = collect(work, (entry) => (entry.kind === "background" || (entry.kind === "hook" && entry.mode === "async")) && entry.status === "running");
  const hookRuns = new Set(work.flatMap((run) => run.entries.flatMap((entry) => entry.kind === "hook" ? entry.activity_ids ?? [] : [])));
  const tasks: ActivitySideGroup[] = [];
  const delegates = new Map<string, ActivitySideGroup>();
  const delegationRuns = new Set(work.filter((run) => run.kind === "delegation").flatMap((run) => [run.id, run.owner_id]).filter(Boolean));
  for (const run of work) {
    if (run.status !== "running") continue;
    if (run.kind === "task") tasks.push({ run, entries: [] });
    if (run.kind === "hook" && !hookRuns.has(run.id)) background.push({ run, entries: [] });
    if (run.kind === "delegation") {
      const key = run.owner_id || run.id;
      const previous = delegates.get(key);
      if (!previous || previous.run.started_at < run.started_at) delegates.set(key, { run, entries: [] });
    }
  }
  for (const run of work) for (const entry of run.entries) {
    if (entry.kind === "delegation" && entry.status === "running" && !delegationRuns.has(entry.id) && !delegationRuns.has(entry.run_id ?? "")) {
      delegates.set(entry.id, { run, entries: [entry] });
    }
  }
  return { recollection, judgments, background, tasks, delegates: [...delegates.values()] };
}
