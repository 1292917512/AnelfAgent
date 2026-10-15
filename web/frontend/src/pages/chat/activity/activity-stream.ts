import type { ActivityEntry, ActivityRun } from "@/lib/types/activity";

export type ActivityThought = Extract<ActivityEntry, { kind: "thinking" }>;
export interface ActivityRow { entry: ActivityEntry; thoughts?: ActivityThought[] }
export interface ActivitySegment { key: string; run: ActivityRun; entries: ActivityRow[]; first: boolean; finished: boolean }

/** 在同一执行内将思考归入所属模型调用，其他步骤保持原有顺序。 */
export function activityRows(entries: ActivityEntry[]): ActivityRow[] {
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
  return rows;
}

/** 按步骤开始时间交织并发过程；流式更新和工具完成不会移动已有步骤。 */
export function activitySegments(runs: ActivityRun[]): ActivitySegment[] {
  const points = runs.flatMap((run, order) => [
    { run, order, index: -1, ts: run.started_at, entry: null, finished: false },
    ...activityRows(run.entries).filter(({ entry }) => entry.kind !== "hook").map((entry, index) => ({ run, order, index, ts: entry.entry.ts, entry, finished: false })),
    ...(run.status !== "running" ? [{ run, order, index: run.entries.length, ts: run.ended_at ?? run.updated_at, entry: null, finished: true }] : []),
  ]).sort((a, b) => a.ts - b.ts || a.order - b.order || a.index - b.index);
  const segments: ActivitySegment[] = [];
  const seen = new Set<string>();
  for (const point of points) {
    let segment = segments[segments.length - 1];
    if (!segment || segment.run.id !== point.run.id) {
      segment = { key: `${point.run.id}:${point.entry?.entry.id ?? (point.finished ? "end" : "start")}`, run: point.run, entries: [], first: !seen.has(point.run.id), finished: false };
      segments.push(segment);
      seen.add(point.run.id);
    }
    if (point.entry) segment.entries.push(point.entry);
    segment.finished ||= point.finished;
  }
  return segments;
}
