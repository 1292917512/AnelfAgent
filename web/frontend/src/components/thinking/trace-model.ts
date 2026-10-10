import type { TFunction } from "i18next";
import type { SessionSummary, ThinkingSession, TraceNode } from "@/lib/types";

export type TraceStage = "prepare" | "reason" | "act" | "finish";
export interface TraceGroup { id: string; anchor: TraceNode | null; nodes: TraceNode[] }
export interface TraceUsage {
  prompt_tokens: number; completion_tokens: number; total_tokens: number;
  cache_read_input_tokens: number; cache_creation_input_tokens: number;
}

export function textValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function nodeUsage(node: TraceNode): TraceUsage {
  const usage = node.data.usage;
  const source = usage && typeof usage === "object" ? usage : {};
  const prompt = numberValue("prompt_tokens" in source ? source.prompt_tokens : 0);
  const completion = numberValue("completion_tokens" in source ? source.completion_tokens : 0);
  return {
    prompt_tokens: prompt, completion_tokens: completion,
    total_tokens: numberValue("total_tokens" in source ? source.total_tokens : 0) || prompt + completion,
    cache_read_input_tokens: numberValue("cache_read_input_tokens" in source ? source.cache_read_input_tokens : 0),
    cache_creation_input_tokens: numberValue("cache_creation_input_tokens" in source ? source.cache_creation_input_tokens : 0),
  };
}

export function durationLabel(milliseconds: number): string {
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)} s`;
  return `${Math.floor(milliseconds / 60_000)} m ${Math.floor(milliseconds / 1000) % 60} s`;
}

export function sessionKind(session: SessionSummary): string {
  return session.is_delegation ? "delegationSession" : session.is_introspection ? "introspection"
    : session.is_heartbeat ? "heartbeat" : "thinkingSession";
}

export function nodeStage(node: TraceNode): TraceStage {
  if (node.type === "session_end") return "finish";
  if (["tool_call", "entity_call", "multi_tool_task", "multi_tool_complete", "fake_tool_call"].includes(node.type)) return "act";
  if (["llm_call", "reply_round", "introspection", "decision"].includes(node.type)) return "reason";
  return "prepare";
}

export function nodeTitle(node: TraceNode, t: TFunction<"thinking">): string {
  if (node.type === "llm_call") return textValue(node.data.model) || t("nodeTypes.llm_call");
  if (node.type === "tool_call") return textValue(node.data.tool_name) || node.label;
  if (node.type === "reply_round") return t("roundTitle", { count: numberValue(node.data.iteration) + 1 });
  if (["session_start", "context_build", "situation", "decision"].includes(node.type)) return t(`nodeTypes.${node.type}`);
  return node.label;
}

export function nodeSummary(node: TraceNode, t: TFunction<"thinking">): string {
  const data = node.data;
  if (textValue(data.error)) return textValue(data.error);
  if (node.type === "decision" && Array.isArray(data.decisions)) {
    return data.decisions.map((value: unknown) => {
      if (!value || typeof value !== "object") return "";
      return [textValue("type" in value ? value.type : ""), textValue("target" in value ? value.target : "")].filter(Boolean).join(" → ");
    }).filter(Boolean).join(" · ");
  }
  if (node.type === "situation") return t("situationSummary", { messages: numberValue(data.message_count), tasks: numberValue(data.task_count) });
  if (node.type === "context_build") return t("contextSummary", { memories: numberValue(data.memory_msgs_count), tools: numberValue(data.tool_count) });
  if (node.type === "llm_call" && textValue(data.reasoning_content)) return textValue(data.reasoning_content);
  if (node.type === "llm_call" && Array.isArray(data.tool_calls) && data.tool_calls.length) {
    return t("callsTools", { tools: data.tool_calls.filter((value): value is string => typeof value === "string").join(", ") });
  }
  return ["result_preview", "content_preview", "preview", "reason", "description", "note"]
    .map((key) => textValue(data[key])).find(Boolean) ?? "";
}

export function traceSummary(session: ThinkingSession) {
  const nodes = session.nodes;
  const calls = nodes.filter((node) => node.type === "llm_call");
  const issues = nodes.filter((node) => node.status === "error" || node.status === "warning");
  const current = !session.ended ? [...nodes].reverse().find((node) => node.status === "running") ?? nodes[nodes.length - 1] : nodes[nodes.length - 1];
  const usage = calls.reduce((total, node) => {
    const next = nodeUsage(node);
    return { tokens: total.tokens + next.total_tokens, input: total.input + next.prompt_tokens, cached: total.cached + next.cache_read_input_tokens };
  }, { tokens: 0, input: 0, cached: 0 });
  return { calls: calls.length, tools: nodes.filter((node) => node.type === "tool_call").length, issues, current, usage };
}

/** Groups nodes by their owning reply round while retaining standalone events in chronological order. */
export function traceGroups(nodes: TraceNode[]): TraceGroup[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const groups: TraceGroup[] = [];
  const rounds = new Map<string, TraceGroup>();
  let standalone: TraceGroup | undefined;
  for (const node of nodes) {
    let current: TraceNode | undefined = node;
    const visited = new Set<string>();
    while (current && current.type !== "reply_round" && !visited.has(current.id)) {
      visited.add(current.id);
      current = current.parent_id ? byId.get(current.parent_id) : undefined;
    }
    if (current?.type === "reply_round") {
      let group = rounds.get(current.id);
      if (!group) {
        group = { id: current.id, anchor: current, nodes: [] };
        rounds.set(current.id, group);
        groups.push(group);
      }
      group.nodes.push(node);
      standalone = undefined;
    } else {
      if (!standalone) {
        standalone = { id: node.id, anchor: null, nodes: [] };
        groups.push(standalone);
      }
      standalone.nodes.push(node);
    }
  }
  return groups;
}
