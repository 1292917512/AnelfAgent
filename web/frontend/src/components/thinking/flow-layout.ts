import type { Edge } from "@xyflow/react";
import type { ThinkingSession, TraceNode } from "@/lib/types";
import type { TraceGraphNode } from "./TraceNode";

/** Places time-ordered roots vertically, with their child calls indented and cycle-safe. */
export function buildFlowElements(session: ThinkingSession | null): { nodes: TraceGraphNode[]; edges: Edge[] } {
  if (!session) return { nodes: [], edges: [] };
  const nodesById = new Map(session.nodes.map((node) => [node.id, node]));
  const children = new Map<string, TraceNode[]>();
  const roots: TraceNode[] = [];
  for (const node of nodesById.values()) {
    if (node.parent_id && node.parent_id !== node.id && nodesById.has(node.parent_id)) {
      const siblings = children.get(node.parent_id) ?? [];
      siblings.push(node);
      children.set(node.parent_id, siblings);
    } else roots.push(node);
  }
  const nodes: TraceGraphNode[] = [];
  const edges: Edge[] = [];
  const visited = new Set<string>();
  function place(root: TraceNode): void {
    const stack = [{ trace: root, depth: 0, parent: "" }];
    while (stack.length) {
      const entry = stack.pop();
      if (!entry || visited.has(entry.trace.id)) continue;
      const { trace, depth, parent } = entry;
      visited.add(trace.id);
      nodes.push({ id: trace.id, type: "trace", position: { x: Math.min(depth, 5) * 290, y: nodes.length * 145 }, data: { trace } });
      if (parent) edges.push({
        id: `child:${trace.id}`, source: parent, target: trace.id, type: "smoothstep",
        animated: trace.status === "running",
        style: { stroke: trace.status === "error" ? "var(--danger)" : "var(--border-strong)", strokeWidth: 1.5 },
      });
      for (const child of [...(children.get(trace.id) ?? [])].reverse()) stack.push({ trace: child, depth: depth + 1, parent: trace.id });
    }
  }
  for (const root of roots) {
    const previous = nodes[nodes.length - 1];
    place(root);
    if (previous) edges.push({ id: `sequence:${root.id}`, source: previous.id, target: root.id, type: "smoothstep",
      style: { stroke: "var(--border-strong)", strokeDasharray: "4 4" } });
  }
  for (const node of nodesById.values()) if (!visited.has(node.id)) place(node);
  return { nodes, edges };
}
