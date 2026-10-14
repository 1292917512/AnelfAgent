import { beforeEach, expect, it, vi } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { useActivityStore } from "./activity-store";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({ api: { get: vi.fn(), delete: vi.fn() } }));
const run = (id: string, revision: number, status = "running"): ActivityRun => ({
  id, revision, status, scope: "group_qq:42", origin_scope: "group_qq:42", source: { scope: "group_qq:42", kind: "group", channel: "qq", target: "42", session: "" },
  actor: "", label: "Review deployment", kind: "conversation", input: "Review deployment", owner_id: "", parent_id: "", started_at: 10, updated_at: 12, ended_at: null, entries: [], entry_count: 0, truncated: false,
});
beforeEach(() => { vi.clearAllMocks(); useActivityStore.setState({ epoch: "", snapshotRevision: 0, runs: [], loaded: false, loading: false, clearing: false, error: null }); });

it("clears earlier records without stale snapshots or queued frames bringing them back", async () => {
  const store = useActivityStore.getState();
  const previous = run("previous", 1, "completed");
  const current = run("current", 2);
  store.restore({ epoch: "p", revision: 2, runs: [previous, current] });
  vi.mocked(api.delete).mockResolvedValueOnce({ data: { epoch: "p", revision: 3, runs: [current] } });
  await store.clearHistory();
  expect(api.delete).toHaveBeenCalledWith("/workspace/activity/history");
  store.restore({ epoch: "p", revision: 2, runs: [previous, current] });
  store.receive("p", previous);
  expect(useActivityStore.getState().runs).toEqual([current]);
  store.receive("p", { ...current, revision: 4, status: "completed" });
  expect(useActivityStore.getState().runs[0]?.status).toBe("completed");
  expect(useActivityStore.getState().clearing).toBe(false);
});

it("keeps all records when clearing fails", async () => {
  const store = useActivityStore.getState();
  store.receive("p", run("current", 1));
  vi.mocked(api.delete).mockRejectedValueOnce(new Error("Offline"));
  await store.clearHistory();
  expect(useActivityStore.getState().runs).toHaveLength(1);
  expect(useActivityStore.getState().error).toBeInstanceOf(Error);
  expect(useActivityStore.getState().clearing).toBe(false);
});

it("merges live updates with a delayed snapshot without reviving completed tools", () => {
  const store = useActivityStore.getState();
  store.receive("process", run("qq", 5, "completed"));
  store.receive("process", run("web", 6));
  store.restore({ epoch: "process", revision: 4, runs: [run("qq", 3)] });
  store.receive("process", run("qq", 2));
  expect(useActivityStore.getState().runs.map((item) => [item.id, item.revision, item.status])).toEqual([["qq", 5, "completed"], ["web", 6, "running"]]);
});

it("shares the pending snapshot request until every caller can read its result", async () => {
  let finish = () => {};
  vi.mocked(api.get).mockImplementationOnce(() => new Promise((resolve) => {
    finish = () => resolve({ data: { epoch: "p", revision: 1, runs: [run("current", 1)] } });
  }));
  const first = useActivityStore.getState().load();
  let secondDone = false;
  const second = useActivityStore.getState().load().then(() => { secondDone = true; });
  await Promise.resolve();
  try {
    expect(secondDone).toBe(false);
    expect(api.get).toHaveBeenCalledTimes(1);
  } finally { finish(); }
  await Promise.all([first, second]);
  expect(secondDone).toBe(true);
  expect(useActivityStore.getState().runs[0]?.id).toBe("current");
});

it("preserves unchanged record and list references across snapshot refreshes", () => {
  const store = useActivityStore.getState();
  const record = run("current", 1);
  store.receive("p", record);
  const records = useActivityStore.getState().runs;
  store.restore({ epoch: "p", revision: 1, runs: [{ ...record, entries: [] }] });
  expect(useActivityStore.getState().runs).toBe(records);
  expect(useActivityStore.getState().runs[0]).toBe(record);
  store.restore({ epoch: "p", revision: 2, runs: [{ ...record, revision: 2, status: "completed" }] });
  expect(useActivityStore.getState().runs[0]?.status).toBe("completed");
});

it("reuses completed steps across streamed progress, entry eviction and terminal snapshots", () => {
  const store = useActivityStore.getState();
  const initial: ActivityRun = { ...run("current", 1), entries: [
    { id: "old", kind: "text", ts: 1, content: "Earlier" },
    { id: "tool", kind: "tool", ts: 2, name: "inspect", arguments: "{}", targets: [{ key: "path", value: "notes.md" }], request_id: "request", status: "done", result: "Complete" },
    { id: "thought", kind: "thinking", ts: 3, content: "Checking" },
  ] };
  store.receive("p", initial);
  const updated: ActivityRun = JSON.parse(JSON.stringify(initial));
  updated.revision = 2;
  updated.entries.shift();
  const thought = updated.entries[1];
  if (thought?.kind === "thinking") thought.content += " results";
  store.receive("p", updated);
  const received = useActivityStore.getState().runs[0]!;
  expect(received.entries[0]).toBe(initial.entries[1]);
  expect(received.entries[1]).not.toBe(initial.entries[2]);
  expect(received.source).toBe(initial.source);
  const finished = { ...JSON.parse(JSON.stringify(received)), status: "completed", revision: 3, ended_at: 15 };
  store.restore({ epoch: "p", revision: 3, runs: [finished] });
  expect(useActivityStore.getState().runs[0]?.status).toBe("completed");
  expect(useActivityStore.getState().runs[0]?.entries[0]).toBe(initial.entries[1]);
  expect(useActivityStore.getState().runs[0]?.entries[1]).toBe(received.entries[1]);
});

it("does not attach an old request failure to a recovered server process", async () => {
  const store = useActivityStore.getState();
  store.restore({ epoch: "old", revision: 1, runs: [] });
  let fail = () => {};
  vi.mocked(api.get).mockImplementationOnce(() => new Promise((_, reject) => {
    fail = () => reject(new Error("Old connection closed"));
  }));
  const request = store.load();
  store.restore({ epoch: "new", revision: 1, runs: [run("recovered", 1)] });
  fail();
  await request;
  expect(useActivityStore.getState().error).toBeNull();
  expect(useActivityStore.getState().runs[0]?.id).toBe("recovered");
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
  expect(useActivityStore.getState().runs).toHaveLength(20);
  expect(useActivityStore.getState().runs[0]?.id).toBe("active");
});

it("also bounds active records and clears every record after a process restart", () => {
  const store = useActivityStore.getState();
  for (let n = 0; n < 50; n++) store.receive("process", { ...run(String(n), n + 1), started_at: n });
  expect(useActivityStore.getState().runs).toHaveLength(20);
  store.restore({ epoch: "restarted", revision: 0, runs: [] });
  expect(useActivityStore.getState().runs).toEqual([]);
});

it("keeps available records and permits retry when a snapshot fails", async () => {
  useActivityStore.getState().receive("p", run("active", 1));
  vi.mocked(api.get).mockRejectedValueOnce(new Error("Offline"));
  await useActivityStore.getState().load();
  expect(useActivityStore.getState().runs).toHaveLength(1);
  expect(useActivityStore.getState().error).toBeInstanceOf(Error);
  expect(useActivityStore.getState().loading).toBe(false);
});
