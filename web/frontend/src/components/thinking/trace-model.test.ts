import { expect, it } from "vitest";
import type { PlanRecord, ThinkingSession, TraceNode } from "@/lib/types";
import { traceGroups, traceSummary } from "./trace-model";
import { buildFlowElements } from "./flow-layout";
import { tracePlanNodes } from "./trace-plans";

function node(id: string, type: string, parent_id: string | null = null): TraceNode {
  return { id, type, parent_id, label: id, timestamp: 5, duration_ms: null, status: "completed", data: {} };
}
const base: ThinkingSession = { id: "S", scope: "user_webui:web_user#A", start_time: 1, end_time: 10, ended: true,
  duration_ms: 9000, is_heartbeat: false, node_count: 0, nodes: [], available_tools: [] };
it("groups parallel nested tools with their owning rounds", () => {
  const groups = traceGroups([node("round1", "reply_round"), node("llm", "llm_call", "round1"),
    node("round2", "reply_round"), node("tool", "tool_call", "llm")]);
  expect(groups.map((group) => group.nodes.map((item) => item.id))).toEqual([["round1", "llm", "tool"], ["round2"]]);
});
it("counts usage only on model calls and preserves warnings separately from completion", () => {
  const call = { ...node("call", "llm_call"), data: { usage: { prompt_tokens: 20, completion_tokens: 5, cache_read_input_tokens: 10 } } };
  const result = traceSummary({ ...base, nodes: [call, { ...node("tool", "tool_call"), status: "warning", data: call.data }] });
  expect(result.usage).toEqual({ tokens: 25, input: 20, cached: 10 });
  expect(result.issues).toHaveLength(1);
});
it("lays out every node once even when parent links form a cycle", () => {
  const result = buildFlowElements({ ...base, nodes: [node("A", "tool_call", "B"), node("B", "tool_call", "A"), node("C", "llm_call")] });
  expect(result.nodes).toHaveLength(3);
  expect(new Set(result.nodes.map((item) => JSON.stringify(item.position))).size).toBe(3);
  expect(result.edges.some((edge) => edge.source === edge.target)).toBe(false);
});
it("never mixes plans from a different conversation or later historical state", () => {
  const plan: PlanRecord = { plan_id: "P", chat_id: "A", goal: "Inspect", steps: [], status: "executing", files: "", risks: "", created_at: 2, updated_at: 3 };
  expect(tracePlanNodes(base, { P: plan })).toHaveLength(1);
  expect(tracePlanNodes(base, { P: { ...plan, chat_id: "B" } })).toEqual([]);
  expect(tracePlanNodes(base, { P: { ...plan, updated_at: 11 } })).toEqual([]);
  expect(tracePlanNodes({ ...base, scope: "user_qq:123" }, { P: plan })).toEqual([]);
});
