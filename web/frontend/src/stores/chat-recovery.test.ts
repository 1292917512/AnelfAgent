import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AxiosHeaders, type AxiosResponse } from "axios";
import { chatApi } from "@/lib/api";
import { useChatStore } from "./chat-store";
import { emptyBucket } from "./chat-shared";
import { useActivityStore } from "./activity-store";

class Source extends EventTarget {
  static CLOSED = 2;
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
}
const sources: Source[] = [];
const response = <T,>(data: T): AxiosResponse<T> => ({ data, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } });

beforeEach(() => {
  vi.useFakeTimers();
  sources.length = 0;
  vi.stubGlobal("EventSource", class extends Source { constructor() { super(); sources.push(this); } });
  vi.spyOn(chatApi, "history").mockResolvedValue(response([]));
  vi.spyOn(chatApi, "folds").mockResolvedValue(response({ folds: [] }));
  vi.spyOn(chatApi, "delegations").mockResolvedValue(response({ running: [] }));
  vi.spyOn(chatApi, "send").mockResolvedValue(response({}));
  vi.spyOn(useActivityStore.getState(), "load").mockResolvedValue();
  useActivityStore.setState({ runs: [], error: null });
  useChatStore.setState({ activeChatId: "A", buckets: { A: emptyBucket(), B: emptyBucket() } });
});
afterEach(() => {
  useChatStore.getState().stopSSE();
  vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});

it("rebuilds a closed stream, recovers replies in every waiting chat and preserves drafts", async () => {
  const store = useChatStore.getState();
  store.startSSE();
  sources[0]?.onopen?.();
  await vi.advanceTimersByTimeAsync(1);
  await store.send("Question A", "User");
  useChatStore.setState({ activeChatId: "B" });
  await store.send("Question B", "User");
  store.setInputDraft("B", "Unsent draft");
  const buckets = useChatStore.getState().buckets;
  vi.mocked(chatApi.history).mockImplementation(async (_user, _limit, id) => response([
    { id: 1, cid: buckets[id ?? "A"]?.messages[0]?.cid, role: "user", content: `Question ${id}`, ts: 1 },
    { id: 2, role: "assistant", content: `Recovered ${id}`, ts: 2 },
  ]));
  sources[0]!.readyState = Source.CLOSED;
  sources[0]?.onerror?.();
  expect(useChatStore.getState().sseConnected).toBe(false);
  await vi.advanceTimersByTimeAsync(5000);
  expect(sources).toHaveLength(2);
  sources[1]?.onopen?.();
  await vi.advanceTimersByTimeAsync(1);
  for (const id of ["A", "B"]) {
    expect(useChatStore.getState().buckets[id]?.messages.map((message) => message.content)).toContain(`Recovered ${id}`);
    expect(useChatStore.getState().buckets[id]?.sending).toBe(false);
  }
  expect(useChatStore.getState().buckets.B?.inputDraft).toBe("Unsent draft");
  sources[1]!.readyState = Source.CLOSED;
  sources[1]?.onerror?.();
  store.stopSSE();
  await vi.advanceTimersByTimeAsync(60000);
  expect(sources).toHaveLength(2);
});

it("does not declare a reply failed while disconnected or while execution is still running", async () => {
  const store = useChatStore.getState();
  store.startSSE();
  sources[0]?.onopen?.();
  await vi.advanceTimersByTimeAsync(1);
  await store.send("Long request", "User");
  sources[0]!.readyState = 0;
  sources[0]?.onerror?.();
  await vi.advanceTimersByTimeAsync(120000);
  expect(useChatStore.getState().buckets.A?.sending).toBe(true);
  expect(useChatStore.getState().buckets.A?.messages).toHaveLength(1);
  sources[0]?.onopen?.();
  sources[0]?.dispatchEvent(new MessageEvent("activity", { data: JSON.stringify({ epoch: "test", run: {
    id: "run", revision: 1, kind: "conversation", status: "running", source: { channel: "webui", kind: "user", session: "A" }, started_at: 1, entries: [],
  } }) }));
  await vi.advanceTimersByTimeAsync(120000);
  expect(useChatStore.getState().buckets.A?.sending).toBe(true);
  expect(useChatStore.getState().buckets.A?.messages).toHaveLength(1);
});

it("resynchronizes lost frames without creating another live connection", async () => {
  useChatStore.getState().startSSE();
  sources[0]?.onopen?.();
  await vi.advanceTimersByTimeAsync(1);
  vi.mocked(chatApi.history).mockClear();
  sources[0]?.dispatchEvent(new MessageEvent("resync", { data: "{}" }));
  await vi.advanceTimersByTimeAsync(1);
  expect(chatApi.history).toHaveBeenCalledOnce();
  expect(sources).toHaveLength(1);
});

it("recovers completed execution after the page resumes and removes its listener on stop", async () => {
  const store = useChatStore.getState();
  store.startSSE();
  store.startSSE();
  sources[0]?.onopen?.();
  await vi.advanceTimersByTimeAsync(1);
  vi.mocked(useActivityStore.getState().load).mockClear();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(useActivityStore.getState().load).not.toHaveBeenCalled();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(useActivityStore.getState().load).toHaveBeenCalledOnce();
  expect(sources).toHaveLength(1);
  store.stopSSE();
  document.dispatchEvent(new Event("visibilitychange"));
  expect(useActivityStore.getState().load).toHaveBeenCalledOnce();
});
