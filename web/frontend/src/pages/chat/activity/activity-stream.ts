import type { ActivityEntry, ActivityRun } from "@/lib/types/activity";

export type ActivityThought = Extract<ActivityEntry, { kind: "thinking" }>;
export interface ActivityRow { entry: ActivityEntry; thoughts?: ActivityThought[] }
export interface ActivitySegment { key: string; run: ActivityRun; entries: ActivityRow[]; first: boolean; finished: boolean; expanded?: boolean }

const entryRows = new WeakMap<ActivityEntry[], ActivityRow[]>();
const stableRows = new WeakMap<ActivityEntry, ActivityRow>();

/** 在同一执行内将思考归入所属模型调用，其他步骤保持原有顺序。 */
export function activityRows(entries: ActivityEntry[]): ActivityRow[] {
  const cached = entryRows.get(entries);
  if (cached) return cached;
  const rows: ActivityRow[] = [];
  let model: ActivityRow | undefined;
  for (const entry of entries) {
    if (entry.kind === "thinking" && model && entry.generation === model.entry.generation) {
      (model.thoughts ??= []).push(entry);
    } else {
      const row = { entry };
      rows.push(row);
      if (entry.kind === "model") model = row;
    }
  }
  const result = rows.map((row) => {
    const previous = stableRows.get(row.entry);
    if (previous && previous.thoughts?.length === row.thoughts?.length
      && (previous.thoughts?.every((thought, index) => thought === row.thoughts?.[index]) ?? true)) return previous;
    stableRows.set(row.entry, row);
    return row;
  });
  entryRows.set(entries, result);
  return result;
}

/** 主执行按步骤时间交织；子代理固定在开始位置，仅主动展开时投影其步骤。 */
export function activitySegments(runs: ActivityRun[], expandedDelegations: readonly string[] = []): ActivitySegment[] {
  const expanded = new Set(expandedDelegations);
  const points = runs.flatMap((run, order) => [
    { run, order, index: -1, ts: run.started_at, entry: null, finished: false },
    ...(run.kind !== "delegation" ? activityRows(run.entries).filter(({ entry }) => entry.kind !== "hook" || (entry.mode === "sync" && entry.event === "context_prepare")).map((entry, index) => ({ run, order, index, ts: entry.entry.ts, entry, finished: false })) : []),
    ...(run.kind !== "delegation" && run.status !== "running" ? [{ run, order, index: run.entries.length, ts: run.ended_at ?? run.updated_at, entry: null, finished: true }] : []),
  ]).sort((a, b) => a.ts - b.ts || a.order - b.order || a.index - b.index);
  const segments: ActivitySegment[] = [];
  const seen = new Set<string>();
  for (const point of points) {
    let segment = segments[segments.length - 1];
    if (!segment || segment.run.id !== point.run.id) {
      const delegation = point.run.kind === "delegation";
      segment = { key: `${point.run.id}:${point.entry?.entry.id ?? (point.finished ? "end" : "start")}`, run: point.run,
        entries: delegation && expanded.has(point.run.id) ? activityRows(point.run.entries) : [], first: !seen.has(point.run.id),
        finished: delegation && point.run.status !== "running", expanded: delegation ? expanded.has(point.run.id) : undefined };
      segments.push(segment);
      seen.add(point.run.id);
    }
    if (point.entry) segment.entries.push(point.entry);
    segment.finished ||= point.finished;
  }
  return segments;
}
