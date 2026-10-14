import type { ActivityEntry, ActivityRun } from "@/lib/types/activity";

export interface ActivityDelegate {
  id: string;
  runs: ActivityRun[];
  parent: ActivityRun;
  entry?: Extract<ActivityEntry, { kind: "delegation" }>;
}

/** 汇总一棵执行树；同一子代理续跑合并计数，排队委派仍保留入口。 */
export function activitySummary(root: ActivityRun, runs: readonly ActivityRun[]) {
  const delegates = new Map<string, ActivityDelegate>();
  const byId = new Map(runs.map((run) => [run.id, run]));
  const plans: { run: ActivityRun; entry: Extract<ActivityEntry, { kind: "plan" }> }[] = [];
  for (const run of runs) {
    if (run.kind === "delegation" && run.id !== root.id) {
      const id = run.owner_id ? `owner:${run.owner_id}` : `run:${run.id}`;
      const item = delegates.get(id) ?? { id, runs: [], parent: byId.get(run.parent_id) ?? root };
      item.runs.push(run);
      delegates.set(id, item);
    }
    for (const entry of run.entries) if (entry.kind === "plan") plans.push({ run, entry });
  }
  for (const run of runs) for (const entry of run.entries) {
    if (entry.kind !== "delegation") continue;
    const child = entry.run_id ? byId.get(entry.run_id) : undefined;
    const id = child ? (child.owner_id ? `owner:${child.owner_id}` : `run:${child.id}`) : `owner:${entry.id}`;
    const item = delegates.get(id) ?? { id, runs: [], parent: run };
    item.entry = entry;
    delegates.set(id, item);
  }
  for (const item of delegates.values()) item.runs.sort((a, b) => a.started_at - b.started_at);
  return {
    delegates: [...delegates.values()], plans,
    steps: plans.reduce((total, { entry }) => total + entry.steps.length, 0),
    done: plans.reduce((total, { entry }) => total + entry.steps.filter((step) => step.status === "completed").length, 0),
  };
}
