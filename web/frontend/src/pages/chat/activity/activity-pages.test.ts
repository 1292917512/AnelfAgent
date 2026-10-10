import { expect, it } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { activityPages } from "./activity-pages";

function run(id: string, start: number, end: number | null, parent_id = ""): ActivityRun {
  return { id, parent_id, started_at: start, ended_at: end, updated_at: end ?? start, status: end == null ? "running" : "completed",
    revision: 1, kind: "conversation", scope: "user_test:1", origin_scope: "user_test:1", source: { scope: "user_test:1", channel: "test", kind: "user", target: "1", session: "" }, actor: "", label: id, input: "", owner_id: "", entries: [], entry_count: 0, truncated: false };
}

it("keeps concurrent channels and late children together while separating older rounds", () => {
  const pages = activityPages([run("old", 1, 2), run("qq", 10, 20), run("telegram", 11, 15), run("child", 21, 30, "qq"), run("new", 35, null)]);
  expect(pages.map((page) => page.runs.map((item) => item.id))).toEqual([["old"], ["qq", "telegram", "child"], ["new"]]);
});

it("retains a background child in the live round after its parent has completed", () => {
  const pages = activityPages([run("parent", 10, 12), run("child", 11, null, "parent"), run("web", 20, 21)]);
  expect(pages).toHaveLength(1);
  expect(pages[0]?.runs.map((item) => item.id)).toEqual(["parent", "child", "web"]);
});

it("renders orphaned children when an older parent was evicted", () => {
  expect(activityPages([run("child", 10, 20, "evicted")])[0]?.runs.map((item) => item.id)).toEqual(["child"]);
});
