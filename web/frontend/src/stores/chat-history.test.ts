import { beforeEach, expect, it, vi } from "vitest";
import { AxiosHeaders, type AxiosResponse } from "axios";
import { chatApi } from "@/lib/api";
import type { ChatBucket, ChatHistoryMessage } from "@/lib/types";
import { createChatHistory } from "./chat-history";
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
  await history.load();
  buckets.A!.messages.push({ role: "user", content: "New message", cid: "local", delivery: "submitting", ts: 2 });
  release(response([{ id: 1, role: "user", content: "Earlier", ts: 1 }]));
  await pending;
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
