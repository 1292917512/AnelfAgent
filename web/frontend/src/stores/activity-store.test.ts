import { beforeEach, expect, it, vi } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { useActivityStore } from "./activity-store";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({ api: { get: vi.fn() } }));
const run = (id: string, revision: number, status = "running"): ActivityRun => ({
  id, revision, status, scope: "group_qq:42", origin_scope: "group_qq:42", source: { scope: "group_qq:42", kind: "group", channel: "qq", target: "42", session: "" },
  actor: "", label: "Review deployment", kind: "conversation", input: "Review deployment", owner_id: "", parent_id: "", started_at: 10, updated_at: 12, ended_at: null, entries: [], entry_count: 0, truncated: false,
});
beforeEach(() => { vi.clearAllMocks(); useActivityStore.setState({ epoch: "", runs: [], loaded: false, loading: false, error: null }); });

it("merges live updates with a delayed snapshot without reviving completed tools", () => {
  const store = useActivityStore.getState();
  store.receive("process", run("qq", 5, "completed"));
  store.receive("process", run("web", 6));
  store.restore({ epoch: "process", revision: 4, runs: [run("qq", 3)] });
  store.receive("process", run("qq", 2));
  expect(useActivityStore.getState().runs.map((item) => [item.id, item.revision, item.status])).toEqual([["qq", 5, "completed"], ["web", 6, "running"]]);
});

it("replaces stale running state when the server process changes", () => {
  const store = useActivityStore.getState();
  store.receive("old", run("old", 100));
  store.restore({ epoch: "new", revision: 1, runs: [run("new", 1)] });
  expect(useActivityStore.getState().runs.map((item) => item.id)).toEqual(["new"]);
});

it("limits completed records without evicting active work", () => {
  const store = useActivityStore.getState();
  store.receive("process", run("active", 1));
  for (let n = 0; n < 50; n++) store.receive("process", { ...run(String(n), n + 2, "completed"), started_at: n + 11 });
  expect(useActivityStore.getState().runs).toHaveLength(31);
  expect(useActivityStore.getState().runs[0]?.id).toBe("active");
});

it("keeps available records and permits retry when a snapshot fails", async () => {
  useActivityStore.getState().receive("p", run("active", 1));
  vi.mocked(api.get).mockRejectedValueOnce(new Error("Offline"));
  await useActivityStore.getState().load();
  expect(useActivityStore.getState().runs).toHaveLength(1);
  expect(useActivityStore.getState().error).toBeInstanceOf(Error);
  expect(useActivityStore.getState().loading).toBe(false);
});
