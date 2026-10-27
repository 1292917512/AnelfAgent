import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AxiosHeaders } from "axios";
import { chatApi, workspaceApi } from "@/lib/api";
import type { ActivityRun } from "@/lib/types";
import { useChatStore } from "./chat-store";
import { useActivityStore } from "./activity-store";
import { emptyBucket } from "./chat-shared";

const run: ActivityRun = { id: "run-A", scope: "scope-A", label: "Task", kind: "conversation", status: "running", revision: 1,
  source: { scope: "scope-A", kind: "user", channel: "webui", target: "web_user", session: "A" },
  origin_scope: "scope-A", actor: "", input: "", owner_id: "", parent_id: "", updated_at: 1, ended_at: null,
  entry_count: 0, truncated: false, started_at: 1, entries: [], can_stop: true };
const response = { data: { status: "ok" as const }, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } };

beforeEach(() => {
  useChatStore.setState({ activeChatId: "A", buckets: { A: { ...emptyBucket(), sending: true }, B: emptyBucket() } });
  useActivityStore.setState({ runs: [run] });
  vi.spyOn(useActivityStore.getState(), "load").mockResolvedValue();
});
afterEach(() => vi.restoreAllMocks());

it("stops the exact visible run once and keeps execution active until server completion", async () => {
  let finish!: (value: typeof response) => void;
  const stop = vi.spyOn(workspaceApi, "controlActivity").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const scopeStop = vi.spyOn(chatApi, "interrupt");
  const pending = useChatStore.getState().interrupt();
  expect(useChatStore.getState().buckets.A?.interruptPending).toBe(true);
  await useChatStore.getState().interrupt();
  useChatStore.setState({ activeChatId: "B" });
  finish(response);
  await pending;
  expect(stop).toHaveBeenCalledExactlyOnceWith("run-A", "stop");
  expect(scopeStop).not.toHaveBeenCalled();
  expect(useChatStore.getState().buckets.A?.sending).toBe(true);
  expect(useChatStore.getState().buckets.A?.interruptPending).toBe(false);
  expect(useChatStore.getState().buckets.B?.interruptPending).toBeUndefined();
});

it("retains the running state after a failed stop and allows retry", async () => {
  vi.spyOn(workspaceApi, "controlActivity").mockRejectedValue(new Error("offline"));
  await useChatStore.getState().interrupt();
  expect(useChatStore.getState().buckets.A?.sending).toBe(true);
  expect(useChatStore.getState().buckets.A?.interruptPending).toBe(false);
});
