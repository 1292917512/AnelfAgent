import { expect, it } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { activityFamilies, activityPages } from "./activity-pages";
import { activitySummary } from "./activity-summary";

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

it("renders orphaned children when an older parent was evicted", () => {
  expect(activityPages([run("child", 10, 20, "evicted")])[0]?.runs.map((item) => item.id)).toEqual(["child"]);
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
