import { beforeEach, expect, it, vi } from "vitest";
import { AxiosHeaders, type AxiosResponse } from "axios";
import { thinkingApi } from "@/lib/api";
import type { ThinkingSession, TraceNode } from "@/lib/types";
import { useThinkingStore } from "./thinking-store";

vi.mock("@/lib/api", () => ({ thinkingApi: { session: vi.fn(), sessions: vi.fn(), status: vi.fn(), toggle: vi.fn() } }));

const node = (id: string, status: TraceNode["status"] = "completed"): TraceNode => ({
  id, type: "tool_call", status, label: id, data: {}, timestamp: 10, duration_ms: null, parent_id: null,
});
const session = (id: string): ThinkingSession => ({
  id, start_time: 1, end_time: null, ended: false, is_heartbeat: false, duration_ms: null, node_count: 1, nodes: [node(id)], available_tools: [],
});
function response<T>(data: T): AxiosResponse<T> {
  return { data, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } };
}
beforeEach(() => {
  vi.clearAllMocks();
  useThinkingStore.getState().shutdown();
  useThinkingStore.setState({ activeSessionId: null, activeSession: null, selectedNodeId: null,
    sessions: [], sessionsLoading: false, sessionsError: null, sessionLoading: false, sessionError: null, autoFollow: true });
});

it("ignores an older session response after a rapid selection change", async () => {
  let release!: (value: AxiosResponse<ThinkingSession>) => void;
  vi.mocked(thinkingApi.session).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  vi.mocked(thinkingApi.session).mockResolvedValueOnce(response(session("B")));
  const old = useThinkingStore.getState().selectSession("A");
  await useThinkingStore.getState().selectSession("B");
  release(response(session("A")));
  await old;
  expect(useThinkingStore.getState().activeSession?.id).toBe("B");
  expect(useThinkingStore.getState().autoFollow).toBe(false);
});

it("retains live updates arriving while a snapshot is loading and deduplicates nodes", async () => {
  let release!: (value: AxiosResponse<ThinkingSession>) => void;
  vi.mocked(thinkingApi.session).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const loading = useThinkingStore.getState().selectSession("A");
  const store = useThinkingStore.getState();
  store.handleNodeAdded({ session_id: "A", node: node("tool", "running") });
  store.handleNodeAdded({ session_id: "A", node: node("tool", "running") });
  store.handleNodeUpdated({ session_id: "A", node_id: "tool", updates: { status: "error", data: { error: "Timed out" } } });
  release(response(session("A")));
  await loading;
  expect(useThinkingStore.getState().activeSession?.nodes).toHaveLength(2);
  expect(useThinkingStore.getState().activeSession?.nodes[1]).toMatchObject({ status: "error", data: { error: "Timed out" } });
});

it("preserves new sessions arriving during a list refresh", async () => {
  let release!: (value: AxiosResponse<{ sessions: ThinkingSession[]; count: number }>) => void;
  vi.mocked(thinkingApi.sessions).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const refresh = useThinkingStore.getState().refreshSessions();
  const current = { ...session("new"), start_time: 20 };
  useThinkingStore.getState().handleSessionStart({ session: current, node: node("new") });
  release(response({ sessions: [session("old")], count: 1 }));
  await refresh;
  expect(useThinkingStore.getState().sessions.map((item) => item.id)).toEqual(["new", "old"]);
  expect(useThinkingStore.getState().activeSessionId).toBe("new");
});

it("reports missing sessions without displaying the previously selected trace", async () => {
  useThinkingStore.setState({ activeSessionId: "A", activeSession: session("A") });
  vi.mocked(thinkingApi.session).mockRejectedValueOnce(new Error("Expired"));
  await useThinkingStore.getState().selectSession("B");
  expect(useThinkingStore.getState().activeSession).toBeNull();
  expect(useThinkingStore.getState().sessionError).toBeInstanceOf(Error);
  vi.mocked(thinkingApi.session).mockResolvedValueOnce(response(session("B")));
  await useThinkingStore.getState().refreshSession();
  expect(useThinkingStore.getState().activeSession?.id).toBe("B");
});

it("does not let background delegation replace an inspected conversation", () => {
  useThinkingStore.setState({ activeSessionId: "A", activeSession: session("A") });
  useThinkingStore.getState().handleSessionStart({ session: { ...session("worker"), is_delegation: true, parent_session_id: "A" }, node: node("worker") });
  expect(useThinkingStore.getState().activeSessionId).toBe("A");
});

it("does not restore sensitive trace data after the authenticated session shuts down", async () => {
  let release!: (value: AxiosResponse<ThinkingSession>) => void;
  vi.mocked(thinkingApi.session).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const pending = useThinkingStore.getState().selectSession("private");
  useThinkingStore.getState().shutdown();
  release(response(session("private")));
  await pending;
  expect(useThinkingStore.getState().activeSession).toBeNull();
  expect(useThinkingStore.getState().activeSessionId).toBeNull();
});

it("does not reopen a stream when an old initialization resolves after logout", async () => {
  let release!: (value: AxiosResponse<{ enabled: boolean }>) => void;
  vi.mocked(thinkingApi.status).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const pending = useThinkingStore.getState().initialize();
  useThinkingStore.getState().shutdown();
  release(response({ enabled: true }));
  await pending;
  expect(useThinkingStore.getState().enabled).toBe(false);
  expect(useThinkingStore.getState().statusSynced).toBe(false);
});

it("refreshes the final trace when recording is disabled in another browser", async () => {
  const completed = { ...session("A"), ended: true, end_time: 2, outcome: "interrupted" as const };
  useThinkingStore.setState({ enabled: true, statusSynced: true, activeSessionId: "A", activeSession: session("A") });
  vi.mocked(thinkingApi.status).mockResolvedValueOnce(response({ enabled: false }));
  vi.mocked(thinkingApi.sessions).mockResolvedValueOnce(response({ sessions: [completed], count: 1 }));
  vi.mocked(thinkingApi.session).mockResolvedValueOnce(response(completed));
  await useThinkingStore.getState().initialize(true);
  expect(useThinkingStore.getState().activeSession?.outcome).toBe("interrupted");
  expect(useThinkingStore.getState().enabled).toBe(false);
});


it("follows root background runs while preserving an explicitly inspected trace", () => {
  const store = useThinkingStore.getState();
  store.handleSessionStart({ session: { ...session("heartbeat"), is_heartbeat: true }, node: node("heartbeat") });
  expect(useThinkingStore.getState().activeSessionId).toBe("heartbeat");
  store.setAutoFollow(false);
  store.handleSessionStart({ session: session("next"), node: node("next") });
  expect(useThinkingStore.getState().activeSessionId).toBe("heartbeat");
});
