import { expect, it } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { activityRows, activitySegments } from "./activity-stream";

function run(id: string, start: number, steps: number[]): ActivityRun {
  return { id, parent_id: "", started_at: start, updated_at: start, ended_at: null, status: "running", revision: 1,
    scope: id, origin_scope: id, source: { scope: id, channel: "test", kind: "user", target: id, session: "" },
    kind: "conversation", actor: "", label: id, input: "", owner_id: "", entry_count: steps.length, truncated: false,
    entries: steps.map((ts) => ({ id: `${id}-${ts}`, kind: "thinking", ts, content: `${id} ${ts}` })) };
}

it("interleaves steps chronologically and keeps their positions when earlier output changes", () => {
  const a = run("A", 1, [2, 6]);
  const b = run("B", 3, [4]);
  const segments = activitySegments([a, b]);
  expect(segments.map((part) => [part.run.id, part.entries.map(({ entry }) => entry.id)])).toEqual([["A", ["A-2"]], ["B", ["B-4"]], ["A", ["A-6"]]]);
  const updated = { ...a, updated_at: 10, entries: a.entries.map((entry) => ({ ...entry, content: "updated" })) };
  expect(activitySegments([updated, b]).map((part) => part.key)).toEqual(segments.map((part) => part.key));
  expect(segments.map((part) => part.first)).toEqual([true, true, false]);
});

it("keeps reasoning with its model across interleaved runs and preserves orphaned reasoning", () => {
  const a = run("A", 1, []);
  a.entries = [
    { id: "orphan", kind: "thinking", ts: 1, generation: 0, content: "Earlier model was pruned" },
    { id: "model-1", kind: "model", ts: 2, generation: 1, name: "first", status: "done" },
    { id: "thought-1", kind: "thinking", ts: 4, generation: 1, content: "First thought" },
    { id: "text-1", kind: "text", ts: 5, generation: 1, content: "Progress" },
    { id: "thought-2", kind: "thinking", ts: 6, generation: 1, content: "More reasoning" },
    { id: "model-2", kind: "model", ts: 8, generation: 2, name: "second", status: "running" },
    { id: "thought-3", kind: "thinking", ts: 9, generation: 2, content: "Second thought" },
  ];
  const rows = activityRows(a.entries);
  expect(rows.map(({ entry }) => entry.id)).toEqual(["orphan", "model-1", "text-1", "model-2"]);
  expect(rows[1]?.thoughts?.map((entry) => entry.id)).toEqual(["thought-1", "thought-2"]);
  expect(rows[3]?.thoughts?.map((entry) => entry.id)).toEqual(["thought-3"]);
  const b = run("B", 3, [7]);
  expect(activitySegments([a, b]).flatMap((segment) => segment.entries).map(({ entry }) => entry.id))
    .toEqual(["orphan", "model-1", "text-1", "B-7", "model-2"]);
  expect(a.entries).toHaveLength(7);
});

it("shows preparation and final state even for runs with no tool or text output", () => {
  const a = { ...run("empty", 1, []), status: "failed", ended_at: 5, error: "Cancelled preparation" };
  expect(activitySegments([a])).toMatchObject([{ first: true, finished: true, entries: [], run: { error: "Cancelled preparation" } }]);
});
