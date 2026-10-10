import { beforeEach, expect, it, vi } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import type { ChatBucket } from "@/lib/types";
import { attachChatSseHandlers } from "./chat-sse-handlers";
import { emptyBucket } from "./chat-shared";
import { useActivityStore } from "./activity-store";

beforeEach(() => { useActivityStore.setState({ epoch: "", runs: [] }); });

it("routes global runtime to activity and only delivered replies to Web messages", () => {
  const events = new EventTarget();
  let bucket: ChatBucket = emptyBucket();
  const updateBucket = vi.fn((_id: string, update: (bucket: ChatBucket) => Partial<ChatBucket>) => { bucket = { ...bucket, ...update(bucket) }; });
  attachChatSseHandlers(events as EventSource, { updateBucket, getActiveChatId: () => "default", setContextUsage: vi.fn() });
  const run: ActivityRun = { id: "qq", scope: "group_qq:42", origin_scope: "group_qq:42", kind: "conversation", label: "Check", input: "Check", owner_id: "", actor: "", parent_id: "", source: { scope: "group_qq:42", kind: "group", channel: "qq", target: "42", session: "" }, status: "running", started_at: 1, updated_at: 1, ended_at: null, revision: 1, entry_count: 1, truncated: false, entries: [{ id: "thought", ts: 1, kind: "thinking", content: "Inspecting all channels" }] };
  events.dispatchEvent(new MessageEvent("activity", { data: JSON.stringify({ epoch: "p", run }) }));
  events.dispatchEvent(new MessageEvent("delta", { data: JSON.stringify({ delta: "Internal output" }) }));
  expect(updateBucket).not.toHaveBeenCalled();
  expect(useActivityStore.getState().runs[0]?.entries).toEqual(run.entries);
  events.dispatchEvent(new MessageEvent("reply", { data: JSON.stringify({ chat_id: "default", content: "Delivered to Web" }) }));
  expect(bucket.messages.map((message) => message.content)).toEqual(["Delivered to Web"]);
  events.dispatchEvent(new MessageEvent("activity_end", { data: JSON.stringify({ epoch: "p", run: { ...run, revision: 2, status: "completed" } }) }));
  expect(useActivityStore.getState().runs[0]?.status).toBe("completed");
  expect(bucket.messages).toHaveLength(1);
});

it("does not reset Web sending state on malformed terminal events", () => {
  const events = new EventTarget();
  const updateBucket = vi.fn();
  attachChatSseHandlers(events as EventSource, { updateBucket, getActiveChatId: () => "default", setContextUsage: vi.fn() });
  events.dispatchEvent(new MessageEvent("turn_end", { data: "broken" }));
  expect(updateBucket).not.toHaveBeenCalled();
});
