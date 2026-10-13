import { expect, it } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { activitySegments } from "./activity-stream";

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
  expect(segments.map((part) => [part.run.id, part.entries.map((entry) => entry.id)])).toEqual([["A", ["A-2"]], ["B", ["B-4"]], ["A", ["A-6"]]]);
  const updated = { ...a, updated_at: 10, entries: a.entries.map((entry) => ({ ...entry, content: "updated" })) };
  expect(activitySegments([updated, b]).map((part) => part.key)).toEqual(segments.map((part) => part.key));
  expect(segments.map((part) => part.first)).toEqual([true, true, false]);
});

it("shows preparation and final state even for runs with no tool or text output", () => {
  const a = { ...run("empty", 1, []), status: "failed", ended_at: 5, error: "Cancelled preparation" };
  expect(activitySegments([a])).toMatchObject([{ first: true, finished: true, entries: [], run: { error: "Cancelled preparation" } }]);
});
