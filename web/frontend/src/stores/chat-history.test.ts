import { beforeEach, expect, it, vi } from "vitest";
import { AxiosHeaders, type AxiosResponse } from "axios";
import { chatApi } from "@/lib/api";
import type { ChatBucket, ChatHistoryMessage } from "@/lib/types";
import { compactHistory, createChatHistory, HISTORY_PAGE_SIZE } from "./chat-history";
import { emptyBucket } from "./chat-shared";

vi.mock("@/lib/api", () => ({ chatApi: { history: vi.fn() } }));
const response = (data: ChatHistoryMessage[]): AxiosResponse<ChatHistoryMessage[]> =>
  ({ data, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } });
let buckets: Record<string, ChatBucket>;
const makeHistory = () => createChatHistory({
  activeId: () => "A", bucket: (id) => buckets[id],
  update: (id, patch) => { const bucket = buckets[id]; if (bucket) buckets[id] = { ...bucket, ...patch(bucket) }; },
  onLoaded: vi.fn(),
});
beforeEach(() => { vi.clearAllMocks(); buckets = { A: emptyBucket(), B: emptyBucket() }; });

it("retains an actionable error and permits retry after initial history loading fails", async () => {
  const history = makeHistory();
  vi.mocked(chatApi.history).mockRejectedValueOnce(new Error("Offline"));
  await history.load();
  expect(buckets.A?.historyLoaded).toBe(false);
  expect(buckets.A?.historyError).toBeInstanceOf(Error);
  expect(buckets.A?.historyLoading).toBe(false);
  vi.mocked(chatApi.history).mockResolvedValueOnce(response([{ id: 1, role: "user", content: "Earlier" }]));
  await history.load();
  expect(buckets.A?.historyLoaded).toBe(true);
  expect(buckets.A?.historyError).toBeNull();
});

it("deduplicates concurrent requests and preserves messages sent while history is loading", async () => {
  const history = makeHistory();
  let release!: (data: AxiosResponse<ChatHistoryMessage[]>) => void;
  vi.mocked(chatApi.history).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const pending = history.load();
  const joined = history.load();
  buckets.A!.messages.push({ role: "user", content: "New message", cid: "local", delivery: "submitting", ts: 2 });
  release(response([{ id: 1, role: "user", content: "Earlier", ts: 1 }]));
  await Promise.all([pending, joined]);
  expect(chatApi.history).toHaveBeenCalledOnce();
  expect(buckets.A?.messages.map((message) => message.content)).toEqual(["Earlier", "New message"]);
});

it("reconciles a submitted message by its client id without duplicating it or losing attachments", async () => {
  const history = makeHistory();
  buckets.A!.messages.push({ role: "user", content: "Message [note](./note.txt)", cid: "client", delivery: "submitted", ts: 2 });
  vi.mocked(chatApi.history).mockResolvedValueOnce(response([{ id: 8, cid: "client", role: "user", content: "Message", ts: 2 }]));
  await history.load();
  expect(buckets.A?.messages).toEqual([expect.objectContaining({ id: 8, cid: "client", content: "Message [note](./note.txt)" })]);
});

it("coalesces reconnect refreshes after an in-flight history response", async () => {
  const history = makeHistory();
  let release!: (data: AxiosResponse<ChatHistoryMessage[]>) => void;
  vi.mocked(chatApi.history).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }))
    .mockResolvedValueOnce(response([{ id: 1, role: "assistant", content: "Arrived during reconnect", ts: 1 }]));
  const pending = history.load();
  let refreshed = false;
  const refreshes = Promise.all([history.refresh(), history.refresh()]).then(() => { refreshed = true; });
  await Promise.resolve();
  expect(refreshed).toBe(false);
  expect(chatApi.history).toHaveBeenCalledOnce();
  release(response([]));
  await Promise.all([pending, refreshes]);
  expect(chatApi.history).toHaveBeenCalledTimes(2);
  expect(buckets.A?.messages[0]?.content).toBe("Arrived during reconnect");
});

it("cancels queued reconnect refreshes with the conversation", async () => {
  const history = makeHistory();
  let release!: (data: AxiosResponse<ChatHistoryMessage[]>) => void;
  vi.mocked(chatApi.history).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const pending = history.load();
  const refresh = history.refresh();
  history.cancel("A");
  release(response([]));
  await Promise.all([pending, refresh]);
  expect(chatApi.history).toHaveBeenCalledOnce();
});

it("does not restore cleared messages or a removed conversation from a late response", async () => {
  const history = makeHistory();
  let release!: (data: AxiosResponse<ChatHistoryMessage[]>) => void;
  vi.mocked(chatApi.history).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const pending = history.load();
  history.cancel("A");
  delete buckets.A;
  release(response([{ id: 1, role: "user", content: "Stale" }]));
  await pending;
  expect(buckets.A).toBeUndefined();
});

it("keeps later local messages while merging a reconnect response", async () => {
  const history = makeHistory();
  buckets.A = { ...emptyBucket(), historyLoaded: true, messages: [{ role: "assistant", content: "Old", ts: 1 }] };
  let release!: (data: AxiosResponse<ChatHistoryMessage[]>) => void;
  vi.mocked(chatApi.history).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const pending = history.refresh();
  buckets.A.messages.push({ role: "assistant", content: "Just arrived", cid: "fresh", ts: 3 });
  release(response([{ id: 1, role: "assistant", content: "Old", ts: 1 }, { id: 2, role: "assistant", content: "Missed", ts: 2 }]));
  await pending;
  expect(buckets.A.messages.map((message) => message.content)).toEqual(["Old", "Missed", "Just arrived"]);
});

it("keeps pagination failures separate and preserves the existing history", async () => {
  const history = makeHistory();
  buckets.A = { ...emptyBucket(), historyLoaded: true, hasMore: true, earliestId: 10, messages: [{ id: 10, role: "user", content: "Current" }] };
  vi.mocked(chatApi.history).mockRejectedValueOnce(new Error("Unavailable"));
  await history.earlier();
  expect(buckets.A.messages).toHaveLength(1);
  expect(buckets.A.earlierError).toBeInstanceOf(Error);
  expect(buckets.A.historyLoaded).toBe(true);
  expect(buckets.A.loadingEarlier).toBe(false);
});

it("reuses unchanged messages across silent reconciliation and only initializes details once", async () => {
  const onLoaded = vi.fn();
  const history = createChatHistory({ activeId: () => "A", bucket: (id) => buckets[id],
    update: (id, patch) => { const bucket = buckets[id]; if (bucket) buckets[id] = { ...bucket, ...patch(bucket) }; }, onLoaded });
  vi.mocked(chatApi.history).mockImplementation(async () => response([{ id: 1, role: "assistant", content: "Unchanged", ts: 1 }]));
  await history.load();
  const messages = buckets.A!.messages;
  await history.refresh();
  await history.refresh();
  expect(buckets.A!.messages).toBe(messages);
  expect(onLoaded).toHaveBeenCalledOnce();
});

it("does not treat a reply to an older message as acknowledgement of newer input", async () => {
  const history = makeHistory();
  buckets.A = { ...emptyBucket(), sending: true, sendingSince: 3, messages: [
    { role: "user", content: "Old", cid: "old", ts: 1 },
    { role: "user", content: "New", cid: "new", ts: 3 },
  ] };
  vi.mocked(chatApi.history).mockResolvedValueOnce(response([
    { id: 1, role: "user", content: "Old", cid: "old", ts: 1 },
    { id: 2, role: "assistant", content: "Older reply", ts: 2 },
  ]));
  await history.refresh();
  expect(buckets.A.sending).toBe(true);
  expect(buckets.A.messages[buckets.A.messages.length - 1]?.cid).toBe("new");
});

it("catches up every missed page using the last committed cursor and retries from a successful page", async () => {
  const history = makeHistory();
  buckets.A = { ...emptyBucket(), historyLoaded: true, latestId: 10, messages: [{ id: 10, role: "user", content: "Start", ts: 10 }] };
  const missed = Array.from({ length: 93 }, (_, i) => ({ id: 11 + i, role: "assistant", content: `Reply ${i}`, ts: 11 + i }));
  vi.mocked(chatApi.history).mockResolvedValueOnce(response(missed.slice(0, HISTORY_PAGE_SIZE))).mockRejectedValueOnce(new Error("Disconnected"));
  await history.refresh();
  expect(buckets.A.latestId).toBe(50);
  expect(buckets.A.historyError).toBeInstanceOf(Error);
  vi.mocked(chatApi.history).mockImplementation(async (_scope, limit, _id, _before, after) => response(missed.filter((message) => message.id > (after ?? 0)).slice(0, limit)));
  await history.refresh();
  expect(buckets.A.messages.map((message) => message.id)).toEqual(Array.from({ length: 94 }, (_, i) => i + 10));
  expect(buckets.A.latestId).toBe(103);
  expect(vi.mocked(chatApi.history).mock.calls.map((call) => call[4])).toEqual([10, 50, 50, 90]);
});

it("merges only confirmed live text and preserves unpersisted replies, media and pending input", async () => {
  const history = makeHistory();
  const media = { role: "assistant", content: "Photo", media_type: "image", url: "/image.png", ts: 4 };
  const pending = { role: "user", content: "Next", cid: "pending", delivery: "unknown" as const, ts: 5 };
  buckets.A = { ...emptyBucket(), historyLoaded: true, latestId: 1, messages: [
    { id: 1, role: "user", content: "Question", ts: 1 }, { role: "assistant", content: "Done", ts: 2 },
    { role: "assistant", content: "Not persisted yet", ts: 3 }, media, pending,
  ] };
  vi.mocked(chatApi.history).mockResolvedValue(response([{ id: 2, role: "assistant", content: "Done", ts: 2 }]));
  await history.refresh();
  await history.refresh();
  expect(buckets.A.messages).toHaveLength(5);
  expect(buckets.A.messages).toContain(media);
  expect(buckets.A.messages).toContain(pending);
  expect(buckets.A.messages.filter((message) => message.content === "Done")).toHaveLength(1);
});

it("releases persisted history without discarding local state and aborts the pending network request", async () => {
  const messages = Array.from({ length: 180 }, (_, id) => ({ id: id + 1, role: "assistant", content: "History" }));
  const local = { role: "user", content: "Draft submission", cid: "local" };
  const bucket = { ...emptyBucket(), messages: [local, ...messages] };
  const compacted = compactHistory(bucket);
  expect(compacted.messages).toHaveLength(121);
  expect(compacted.messages?.[0]).toBe(local);
  expect(compacted.earliestId).toBe(61);
  expect(compacted.hasMore).toBe(true);
  expect(bucket.messages).toHaveLength(181);
  const history = makeHistory();
  let release!: (value: AxiosResponse<ChatHistoryMessage[]>) => void;
  vi.mocked(chatApi.history).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const loading = history.load();
  const signal = vi.mocked(chatApi.history).mock.calls[0]?.[5];
  history.cancel("A");
  expect(signal?.aborted).toBe(true);
  release(response(messages));
  await loading;
  expect(buckets.A?.messages).toHaveLength(0);
});
