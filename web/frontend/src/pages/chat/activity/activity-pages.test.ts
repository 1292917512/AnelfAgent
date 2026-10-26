import { expect, it } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { activityFamilies, activityPages } from "./activity-pages";
import { activitySummary } from "./activity-summary";
import { activityFileChanges } from "./activity-changes";
import { activitySideGroups } from "./activity-side";

function run(id: string, start: number, end: number | null, parent_id = ""): ActivityRun {
  return { id, parent_id, started_at: start, ended_at: end, updated_at: end ?? start, status: end == null ? "running" : "completed",
    revision: 1, kind: "conversation", scope: "user_test:1", origin_scope: "user_test:1", source: { scope: "user_test:1", channel: "test", kind: "user", target: "1", session: "" }, actor: "", label: id, input: "", owner_id: "", entries: [], entry_count: 0, truncated: false };
}

it("keeps parent and late children together without merging unrelated sessions by time", () => {
  const pages = activityPages([run("old", 1, 2), run("qq", 10, 20), run("telegram", 11, 15), run("child", 21, 30, "qq"), run("new", 35, null)]);
  expect(pages.map((page) => page.runs.map((item) => item.id))).toEqual([["old"], ["qq", "child"], ["telegram"], ["new"]]);
});

it("lets finished unrelated rounds leave the live page while a background child is still running", () => {
  const pages = activityPages([run("parent", 10, 12), run("child", 11, null, "parent"), run("web-old", 20, 21), run("web-new", 30, null)]);
  expect(pages.map((page) => page.runs.map((item) => item.id))).toEqual([["web-old"], ["parent", "child", "web-new"]]);
});

it("retains a background child in the live round after its parent has completed", () => {
  const pages = activityPages([run("parent", 10, 12), run("child", 11, null, "parent"), run("web", 20, 21)]);
  expect(pages).toHaveLength(1);
  expect(pages[0]?.runs.map((item) => item.id)).toEqual(["parent", "child", "web"]);
});

it("retains queued delegation work in the live page after the parent reply ends", () => {
  const parent = run("parent", 1, 2);
  parent.entries = [{ id: "queued", kind: "delegation", ts: 1, agent: "Worker", goal: "Waiting", status: "queued", background: true }];
  expect(activityPages([parent, run("new", 3, 4)])[0]?.runs.map((item) => item.id)).toEqual(["parent", "new"]);
});

it("renders orphaned children when an older parent was evicted", () => {
  expect(activityPages([run("child", 10, 20, "evicted")])[0]?.runs.map((item) => item.id)).toEqual(["child"]);
});

it("keeps recollection history but lists only running work, including children of completed rounds", () => {
  const parent = run("parent", 1, 2);
  parent.entries = [
    { id: "recall", kind: "hook", name: "memory.initial", ts: 1, status: "done", mode: "sync", event: "before_reply", source: "internal", scope: parent.scope, duration_ms: 20, detail: "Done" },
    { id: "active", kind: "background", task_kind: "tool", ts: 1, status: "running", description: "Waiting" },
    { id: "done", kind: "background", task_kind: "tool", ts: 1, status: "done", description: "Done" },
    { id: "review", kind: "hook", name: "skill_review", ts: 1, status: "running", mode: "async", event: "after_reply", source: "code", scope: parent.scope, duration_ms: 0, detail: "Review", activity_ids: ["hook-run"] },
    { id: "delegate", kind: "delegation", ts: 1, status: "running", agent: "Review", goal: "Review", run_id: "child", background: true },
    { id: "queued", kind: "delegation", ts: 1, status: "queued", agent: "Queued", goal: "Wait", background: true },
  ];
  const task = { ...run("task", 2, null), kind: "task" };
  const child = { ...run("child", 2, null, "parent"), kind: "delegation", owner_id: "delegate" };
  const nested = { ...run("nested", 3, null, "child"), kind: "delegation", owner_id: "nested" };
  const hook = { ...run("hook-run", 2, null, "parent"), kind: "hook" };
  const all = [parent, task, child, nested, hook, { ...run("finished", 1, 2), kind: "task" }];
  const groups = activitySideGroups([parent], all);
  expect(groups.recollection.flatMap(({ entries }) => entries.map((entry) => entry.id))).toEqual(["recall"]);
  expect(groups.background.flatMap(({ entries }) => entries.map((entry) => entry.id))).toEqual(["active", "review"]);
  expect(groups.tasks.map(({ run }) => run.id)).toEqual(["task"]);
  expect(groups.delegates.map(({ run }) => run.id)).toEqual(["child", "nested", "parent"]);
  expect(activitySideGroups([], all).recollection).toEqual([]);
  expect(activitySideGroups([parent], [parent]).delegates.flatMap(({ entries }) => entries.map((entry) => entry.id))).toEqual(["delegate", "queued"]);
  const ended = all.map((run) => ({ ...run, status: "completed", entries: run.entries.map((entry) => "status" in entry ? { ...entry, status: "done" as const } : entry) }));
  expect(activitySideGroups(ended, ended)).toMatchObject({ tasks: [], delegates: [], background: [] });
});

it("deduplicates resumed subagents and removes stale parent entries once the child finishes", () => {
  const parent = run("parent", 1, 2);
  parent.entries = [{ id: "delegate", kind: "delegation", ts: 1, status: "running", agent: "Worker", goal: "Work", run_id: "child", background: true }];
  const child = { ...run("child", 2, 3, "parent"), kind: "delegation", owner_id: "delegate" };
  const resumed = { ...run("resumed", 4, null, "parent"), kind: "delegation", owner_id: "delegate" };
  expect(activitySideGroups([], [parent, child]).delegates).toEqual([]);
  expect(activitySideGroups([], [parent, child, resumed]).delegates.map(({ run }) => run.id)).toEqual(["resumed"]);
  expect(activitySideGroups([], [resumed]).delegates.map(({ run }) => run.id)).toEqual(["resumed"]);
  parent.entries = [{ id: "unlinked", kind: "delegation", ts: 5, status: "running", agent: "Worker", goal: "Waiting for execution snapshot", background: true }];
  expect(activitySideGroups([], [parent, { ...resumed, owner_id: "" }]).delegates).toHaveLength(2);
});

it("summarizes only the selected family, merging continuations and retaining queued children", () => {
  const root = run("qq", 1, null);
  const child = { ...run("child", 2, 3, "qq"), kind: "delegation", owner_id: "delegate" };
  const continuation = { ...run("continuation", 4, null, "qq"), kind: "delegation", owner_id: "delegate" };
  const nested = { ...run("nested", 5, null, "continuation"), kind: "delegation", owner_id: "nested" };
  const hook = { ...run("hook", 5, null, "qq"), kind: "hook" };
  const other = run("web", 6, null);
  root.entries = [
    { id: "delegate", kind: "delegation", ts: 2, agent: "Reviewer", goal: "Review", status: "running", background: true, run_id: "continuation" },
    { id: "queued", kind: "delegation", ts: 3, agent: "Queued", goal: "Check", status: "queued", background: true },
    { id: "plan", kind: "plan", ts: 1, goal: "Inspect", status: "running", steps: [{ content: "Check", status: "completed", note: "" }, { content: "Review", status: "in_progress", note: "" }] },
  ];
  const family = activityFamilies([root, child, continuation, nested, hook, other]).find((item) => item.id === "qq")!;
  const summary = activitySummary(root, family.runs);
  expect(summary.delegates.map((item) => item.id)).toEqual(["owner:delegate", "owner:nested", "owner:queued"]);
  expect(summary.delegates[0]?.runs.map((item) => item.id)).toEqual(["child", "continuation"]);
  expect(summary.delegates[1]?.parent.id).toBe("continuation");
  expect(summary.delegates[2]?.runs).toEqual([]);
  expect([summary.steps, summary.done]).toEqual([2, 1]);
  expect(activitySummary(other, [other]).delegates).toEqual([]);
  expect(activitySummary(other, [other]).plans).toEqual([]);
});

it("groups file edits across child runs and carries history along renames without copying entries", () => {
  const parent = run("parent", 1, 5);
  parent.entries = [
    { id: "a", ts: 1, kind: "file", path: "a.ts", diff: "+a", additions: 1, removals: 0 },
    { id: "c", ts: 4, kind: "file", path: "b.ts", move_from: "a.ts", diff: "", additions: 0, removals: 0 },
  ];
  const child = run("child", 2, 3, "parent");
  child.entries = [{ id: "b", ts: 2, kind: "file", path: "a.ts", diff: "+b", additions: 2, removals: 1 }];
  const files = activityFileChanges([parent, child]);
  expect(files).toHaveLength(1);
  expect(files[0]).toMatchObject({ path: "b.ts", additions: 3, removals: 1 });
  expect(files[0]?.edits.map(({ entry }) => entry.id)).toEqual(["a", "b", "c"]);
  expect(files[0]?.edits[1]?.entry).toBe(child.entries[0]);
  expect(activityFileChanges([child])[0]?.path).toBe("a.ts");
  expect(parent.entries[0]).toMatchObject({ path: "a.ts" });
});
