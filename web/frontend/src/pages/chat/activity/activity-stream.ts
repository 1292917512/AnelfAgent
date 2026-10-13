import type { ActivityEntry, ActivityRun } from "@/lib/types/activity";

export interface ActivitySegment { key: string; run: ActivityRun; entries: ActivityEntry[]; first: boolean; finished: boolean }

/** 按步骤开始时间交织并发过程；流式更新和工具完成不会移动已有步骤。 */
export function activitySegments(runs: ActivityRun[]): ActivitySegment[] {
  const points = runs.flatMap((run, order) => [
    { run, order, index: -1, ts: run.started_at, entry: null, finished: false },
    ...run.entries.map((entry, index) => ({ run, order, index, ts: entry.ts, entry, finished: false })),
    ...(run.status !== "running" ? [{ run, order, index: run.entries.length, ts: run.ended_at ?? run.updated_at, entry: null, finished: true }] : []),
  ]).sort((a, b) => a.ts - b.ts || a.order - b.order || a.index - b.index);
  const segments: ActivitySegment[] = [];
  const seen = new Set<string>();
  for (const point of points) {
    let segment = segments[segments.length - 1];
    if (!segment || segment.run.id !== point.run.id) {
      segment = { key: `${point.run.id}:${point.entry?.id ?? (point.finished ? "end" : "start")}`, run: point.run, entries: [], first: !seen.has(point.run.id), finished: false };
      segments.push(segment);
      seen.add(point.run.id);
    }
    if (point.entry) segment.entries.push(point.entry);
    segment.finished ||= point.finished;
  }
  return segments;
}
