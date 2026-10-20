import type { ActivityEntry } from "@/lib/types/activity";
import type { ActivitySegment } from "./activity-stream";

export type StageKind = "context" | "model" | "tool" | "text" | "plan" | "notice" | "phase";
export interface ActivityStage {
  id: string; runId: string; entryId: string; entryIds: string[]; kind: StageKind; title: string; preview: string; count: number; running: boolean; failed: boolean;
}

/** 阶段来自现有执行顺序；连续工具合并为刻度，不复制正文或建立第二套状态。 */
export function activityStages(segments: ActivitySegment[]): ActivityStage[] {
  const stages: ActivityStage[] = [];
  for (const segment of segments) {
    for (const { entry } of segment.entries) {
      if (entry.kind === "hook") continue;
      const kind: StageKind = entry.kind === "thinking" ? "model" : entry.kind === "file" || entry.kind === "delegation" || entry.kind === "background" ? "tool" : entry.kind;
      const last = stages[stages.length - 1];
      const running = "status" in entry && entry.status === "running";
      const failed = "status" in entry && ["error", "failed", "blocked"].includes(entry.status);
      if (last && last.runId === segment.run.id && last.kind === kind && kind !== "model") {
        last.count++;
        last.entryIds.push(entry.id);
        last.running ||= running;
        last.failed ||= failed;
        continue;
      }
      stages.push({ id: `${segment.run.id}:${entry.id}`, runId: segment.run.id, entryId: entry.id, entryIds: [entry.id], kind,
        title: segment.run.label || segment.run.actor || segment.run.source.scope,
        preview: stagePreview(entry), count: 1, running, failed });
    }
  }
  return stages;
}

function stagePreview(entry: ActivityEntry): string {
  if (entry.kind === "background") return entry.description;
  if (entry.kind === "phase") return entry.detail || entry.error || "";
  if (entry.kind === "tool" || entry.kind === "model") return entry.name;
  if (entry.kind === "thinking" || entry.kind === "text") return entry.content.slice(0, 180);
  if (entry.kind === "plan" || entry.kind === "delegation") return entry.goal.slice(0, 180);
  if (entry.kind === "context") return entry.blocks.map((block) => block.label).join(" · ");
  if (entry.kind === "notice") return entry.items[0]?.content.slice(0, 180) ?? "";
  return "";
}
