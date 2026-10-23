import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ActivityDelta, ActivityRun } from "@/lib/types/activity";
import type { ChatBucket } from "@/lib/types";
import { attachChatSseHandlers } from "./chat-sse-handlers";
import { emptyBucket } from "./chat-shared";
import { useActivityStore } from "./activity-store";
import { useChangesStore } from "./changes-store";
import { useTreeChangesStore } from "./tree-changes-store";
import { useFileEditorStore } from "./file-editor-store";
import { workspaceFileId } from "@/lib/workspace-file";

beforeEach(() => { vi.useFakeTimers(); useActivityStore.setState({ epoch: "", snapshotRevision: 0, runs: [] }); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

it("bounds file signal history while preserving open editors and repeated edits within one millisecond", () => {
  const events = new EventTarget();
  const detach = attachChatSseHandlers(events as EventSource, { updateBucket: vi.fn(), getActiveChatId: () => "default", setContextUsage: vi.fn() }, () => true, vi.fn());
  useChangesStore.setState({ fileVersions: {}, revision: 0 });
  useTreeChangesStore.getState().clear();
  const file = { path: "open.md", name: "open.md", content: "saved", version: "v1", size: 5, modified: 0, binary: false, truncated: false };
  useFileEditorStore.setState({ tabs: new Map([[workspaceFileId({ root: "workspace", path: file.path }), { file, draft: "Keep draft" }]]) });
  const emit = (path: string) => events.dispatchEvent(new MessageEvent("file_diff", { data: JSON.stringify({ path, additions: 1, removals: 0 }) }));
  try {
    emit("open.md");
    const revision = useChangesStore.getState().fileVersions["open.md"];
    for (let i = 0; i < 1500; i++) emit(`part-${i}.ts`);
    expect(Object.keys(useChangesStore.getState().fileVersions)).toHaveLength(512);
    expect(Object.keys(useTreeChangesStore.getState().entries)).toHaveLength(200);
    expect(useChangesStore.getState().fileVersions["open.md"]).toBe(revision);
    emit("open.md");
    expect(useChangesStore.getState().fileVersions["open.md"]).toBeGreaterThan(revision!);
    const entry = useTreeChangesStore.getState().entries["open.md"]!;
    emit("open.md");
    expect(useTreeChangesStore.getState().entries["open.md"]).toMatchObject({ ts: entry.ts, additions: 2 });
    expect(useTreeChangesStore.getState().entries["open.md"]!.revision).toBeGreaterThan(entry.revision);
    expect([...useFileEditorStore.getState().tabs.values()][0]?.draft).toBe("Keep draft");
  } finally {
    detach(); useFileEditorStore.setState({ tabs: new Map() }); useTreeChangesStore.getState().clear();
    useChangesStore.setState({ fileVersions: {}, revision: 0 });
  }
});

it("rebuilds connection deltas independently of newer HTTP snapshots and never duplicates text", () => {
  const events = new EventTarget();
  const recover = vi.fn();
  attachChatSseHandlers(events as EventSource, { updateBucket: vi.fn(), getActiveChatId: () => "default", setContextUsage: vi.fn() }, () => true, recover);
  const emit = (name: string, data: unknown) => { events.dispatchEvent(new MessageEvent(name, { data: JSON.stringify(data) })); vi.advanceTimersByTime(20); };
  const run: ActivityRun = { id: "run", revision: 1, scope: "", origin_scope: "", source: { scope: "", channel: "", kind: "", target: "", session: "" },
    actor: "", label: "Inspect", kind: "conversation", input: "Inspect", owner_id: "", parent_id: "", started_at: 1, updated_at: 1, ended_at: null,
    status: "running", entry_count: 2, truncated: false, entries: [
      { id: "thought", kind: "thinking", ts: 1, content: "A" },
      { id: "tool", kind: "tool", ts: 2, name: "ssh_exec", arguments: "{}", targets: [], request_id: "r", status: "running" },
    ] };
  emit("activity", { epoch: "p", run });
  useActivityStore.getState().restore({ epoch: "p", revision: 3, runs: [{ ...run, revision: 3, entries: [{ ...run.entries[0]!, kind: "thinking", content: "ABC" }, run.entries[1]!] }] });
  const delta: ActivityDelta = { epoch: "p", base_revision: 1, run: { id: "run", revision: 2 }, entries: [], appends: [{ id: "thought", text: "B" }] };
  emit("activity_delta", delta);
  expect(useActivityStore.getState().runs[0]?.entries[0]).toMatchObject({ content: "ABC" });
  const finished: ActivityDelta = { ...delta, base_revision: 2, run: { id: "run", revision: 4, status: "completed", ended_at: 100 },
    appends: [{ id: "thought", text: "CD🙂" }], entries: [{ id: "tool", kind: "tool", ts: 2, name: "ssh_exec", arguments: "{}", targets: [], request_id: "r", status: "error", result: "Timed out", duration_ms: 90019 }] };
  emit("activity_delta", finished);
  emit("activity_delta", finished);
  expect(useActivityStore.getState().runs[0]).toMatchObject({ status: "completed", entries: [{ content: "ABCD🙂" }, { status: "error", result: "Timed out", duration_ms: 90019 }] });
  emit("activity_delta", { ...delta, base_revision: 4, run: { id: "run", revision: 5 }, appends: [], entry_order: ["tool"] });
  expect(useActivityStore.getState().runs[0]?.entries.map((entry) => entry.id)).toEqual(["tool"]);
  expect(recover).not.toHaveBeenCalled();
  emit("activity_delta", { ...delta, base_revision: 6, run: { id: "run", revision: 7 } });
  expect(recover).toHaveBeenCalledOnce();
  expect(useActivityStore.getState().runs[0]?.revision).toBe(5);
});

it("routes global runtime to activity and only delivered replies to Web messages", () => {
  const events = new EventTarget();
  let bucket: ChatBucket = emptyBucket();
  const updateBucket = vi.fn((_id: string, update: (bucket: ChatBucket) => Partial<ChatBucket>) => { bucket = { ...bucket, ...update(bucket) }; });
  attachChatSseHandlers(events as EventSource, { updateBucket, getActiveChatId: () => "default", setContextUsage: vi.fn() }, () => true, vi.fn());
  const run: ActivityRun = { id: "qq", scope: "group_qq:42", origin_scope: "group_qq:42", kind: "conversation", label: "Check", input: "Check", owner_id: "", actor: "", parent_id: "", source: { scope: "group_qq:42", kind: "group", channel: "qq", target: "42", session: "" }, status: "running", started_at: 1, updated_at: 1, ended_at: null, revision: 1, entry_count: 1, truncated: false, entries: [{ id: "thought", ts: 1, kind: "thinking", content: "Inspecting all channels" }] };
  events.dispatchEvent(new MessageEvent("activity", { data: JSON.stringify({ epoch: "p", run }) }));
  vi.advanceTimersByTime(20);
  events.dispatchEvent(new MessageEvent("delta", { data: JSON.stringify({ delta: "Internal output" }) }));
  expect(updateBucket).not.toHaveBeenCalled();
  expect(useActivityStore.getState().runs[0]?.entries).toEqual(run.entries);
  events.dispatchEvent(new MessageEvent("reply", { data: JSON.stringify({ chat_id: "default", content: "Delivered to Web" }) }));
  expect(bucket.messages.map((message) => message.content)).toEqual(["Delivered to Web"]);
  events.dispatchEvent(new MessageEvent("activity_end", { data: JSON.stringify({ epoch: "p", run: { ...run, revision: 2, status: "completed" } }) }));
  expect(useActivityStore.getState().runs[0]?.status).toBe("completed");
  events.dispatchEvent(new MessageEvent("activity_snapshot", { data: JSON.stringify({ epoch: "p", revision: 3, runs: [] }) }));
  expect(useActivityStore.getState().runs).toEqual([]);
  events.dispatchEvent(new MessageEvent("activity_end", { data: JSON.stringify({ epoch: "p", run: { ...run, revision: 2, status: "completed" } }) }));
  expect(useActivityStore.getState().runs).toEqual([]);
  expect(bucket.messages).toHaveLength(1);
});

it("does not reset Web sending state on malformed terminal events", () => {
  const events = new EventTarget();
  const updateBucket = vi.fn();
  attachChatSseHandlers(events as EventSource, { updateBucket, getActiveChatId: () => "default", setContextUsage: vi.fn() }, () => true, vi.fn());
  events.dispatchEvent(new MessageEvent("turn_end", { data: "broken" }));
  expect(updateBucket).not.toHaveBeenCalled();
});

it("ignores queued events from a replaced connection including old server epochs", () => {
  const events = new EventTarget();
  const updateBucket = vi.fn();
  let current = true;
  attachChatSseHandlers(events as EventSource, { updateBucket, getActiveChatId: () => "default", setContextUsage: vi.fn() }, () => current, vi.fn());
  current = false;
  useActivityStore.setState({ epoch: "new" });
  events.dispatchEvent(new MessageEvent("activity_snapshot", { data: JSON.stringify({ epoch: "old", revision: 500, runs: [] }) }));
  events.dispatchEvent(new MessageEvent("reply", { data: JSON.stringify({ content: "Late reply" }) }));
  events.dispatchEvent(new MessageEvent("turn_end", { data: "{}" }));
  expect(useActivityStore.getState().epoch).toBe("new");
  expect(updateBucket).not.toHaveBeenCalled();
});

it("coalesces live frames, delivers terminal state immediately and discards a replaced connection's pending frame", () => {
  const events = new EventTarget();
  let current = true;
  attachChatSseHandlers(events as EventSource, { updateBucket: vi.fn(), getActiveChatId: () => "default", setContextUsage: vi.fn() }, () => current, vi.fn());
  const run: ActivityRun = { id: "run", revision: 1, scope: "", origin_scope: "", source: { scope: "", channel: "", kind: "", target: "", session: "" },
    actor: "", label: "Inspect", kind: "reflection", input: "", owner_id: "", parent_id: "", started_at: 1, updated_at: 1, ended_at: null,
    status: "running", entry_count: 1, truncated: false, entries: [{ id: "text", kind: "text", ts: 1, content: "A" }] };
  const changed = vi.fn();
  const unsubscribe = useActivityStore.subscribe(changed);
  try {
    for (let revision = 1; revision <= 50; revision++) events.dispatchEvent(new MessageEvent("activity", {
      data: JSON.stringify({ epoch: "p", run: { ...run, revision, entries: [{ ...run.entries[0], content: `Result ${revision}` }] } }),
    }));
    expect(changed).not.toHaveBeenCalled();
    vi.advanceTimersByTime(20);
    expect(changed).toHaveBeenCalledOnce();
    expect(useActivityStore.getState().runs[0]?.entries[0]).toMatchObject({ content: "Result 50" });
    events.dispatchEvent(new MessageEvent("activity_end", { data: JSON.stringify({ epoch: "p", run: { ...run, revision: 51, status: "cancelled" } }) }));
    expect(useActivityStore.getState().runs[0]?.status).toBe("cancelled");
    events.dispatchEvent(new MessageEvent("activity", { data: JSON.stringify({ epoch: "p", run: { ...run, id: "late", revision: 52 } }) }));
    current = false;
    vi.advanceTimersByTime(20);
    expect(useActivityStore.getState().runs.map((item) => item.id)).toEqual(["run"]);
    current = true;
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    changed.mockClear();
    for (let revision = 53; revision <= 102; revision++) events.dispatchEvent(new MessageEvent("activity", {
      data: JSON.stringify({ epoch: "p", run: { ...run, revision, entries: [{ ...run.entries[0], content: `Hidden ${revision}` }] } }),
    }));
    expect(changed).not.toHaveBeenCalled();
    vi.advanceTimersByTime(250);
    expect(changed).toHaveBeenCalledOnce();
    expect(useActivityStore.getState().runs[0]?.entries[0]).toMatchObject({ content: "Hidden 102" });
    events.dispatchEvent(new MessageEvent("activity_end", { data: JSON.stringify({ epoch: "p", run: { ...run, revision: 103, status: "completed" } }) }));
    expect(useActivityStore.getState().runs[0]?.status).toBe("completed");
  } finally { unsubscribe(); }
});
