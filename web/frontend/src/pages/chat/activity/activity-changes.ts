import type { ActivityFileChange, ActivityRun } from "@/lib/types";

/** 从当前轮次的既有记录派生文件列表；重命名沿路径迁移，不另存差异正文。 */
export function activityFileChanges(runs: ActivityRun[]): ActivityFileChange[] {
  const files = new Map<string, ActivityFileChange>();
  const edits = runs.flatMap((run) => run.entries.flatMap((entry) => entry.kind === "file" ? [{ run, entry }] : []))
    .sort((a, b) => a.entry.ts - b.entry.ts || a.entry.id.localeCompare(b.entry.id));
  for (const edit of edits) {
    const { path, move_from, additions, removals } = edit.entry;
    let file = files.get(path);
    if (move_from && move_from !== path) {
      const previous = files.get(move_from);
      if (previous) {
        files.delete(move_from);
        file = { path, edits: [...previous.edits, ...(file?.edits ?? [])], additions: previous.additions + (file?.additions ?? 0), removals: previous.removals + (file?.removals ?? 0) };
      }
    }
    file ??= { path, edits: [], additions: 0, removals: 0 };
    file.edits.push(edit);
    file.additions += additions;
    file.removals += removals;
    files.set(path, file);
  }
  return [...files.values()];
}
