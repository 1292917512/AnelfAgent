import type { Node, Edge } from "@xyflow/react";
import type { ThinkingSession, TraceNode } from "@/stores/thinking-store";

const COL_W = 340;
const ROW_H = 130;

function edgeStroke(status: string): string {
  if (status === "error") return "var(--danger)";
  return "var(--border-strong)";
}

/**
 * 递归子树布局：根节点（时间序列事件）横向排列，子树向下展开，
 * 父节点居中于子节点上方，支持任意深度嵌套且不重叠。
 */
export function buildFlowElements(session: ThinkingSession | null): { nodes: Node[]; edges: Edge[] } {
  if (!session) return { nodes: [], edges: [] };

  const ids = new Set(session.nodes.map((n) => n.id));
  const childrenMap = new Map<string, TraceNode[]>();
  const roots: TraceNode[] = [];
  for (const n of session.nodes) {
    if (n.parent_id && ids.has(n.parent_id)) {
      const list = childrenMap.get(n.parent_id) ?? [];
      list.push(n);
      childrenMap.set(n.parent_id, list);
    } else {
      roots.push(n);
    }
  }

  const positions = new Map<string, { x: number; y: number }>();
  let leafCol = 0;

  const layout = (node: TraceNode, depth: number): number => {
    const children = childrenMap.get(node.id) ?? [];
    let col: number;
    if (children.length === 0) {
      col = leafCol++;
    } else {
      let first = Infinity;
      let last = -Infinity;
      for (const child of children) {
        const childCol = layout(child, depth + 1);
        first = Math.min(first, childCol);
        last = Math.max(last, childCol);
      }
      col = (first + last) / 2;
    }
    positions.set(node.id, { x: col * COL_W, y: depth * ROW_H });
    return col;
  };
  for (const root of roots) layout(root, 0);

  const nodes: Node[] = session.nodes.map((n) => ({
    id: n.id,
    type: "trace",
    position: positions.get(n.id) ?? { x: 0, y: 0 },
    data: {
      label: n.label,
      nodeType: n.type,
      status: n.status,
      duration_ms: n.duration_ms,
      data: n.data,
    },
  }));

  const edges: Edge[] = [];
  for (const n of session.nodes) {
    if (n.parent_id && ids.has(n.parent_id)) {
      edges.push({
        id: `e-${n.parent_id}-${n.id}`,
        source: n.parent_id,
        target: n.id,
        animated: n.status === "running",
        style: { stroke: edgeStroke(n.status), strokeWidth: 1.5 },
      });
    }
  }
  // 相邻根节点的时间序连接（虚线）
  for (let i = 1; i < roots.length; i++) {
    const prev = roots[i - 1]!;
    const cur = roots[i]!;
    edges.push({
      id: `e-seq-${prev.id}-${cur.id}`,
      source: prev.id,
      target: cur.id,
      animated: cur.status === "running",
      style: { stroke: edgeStroke(cur.status), strokeWidth: 1.5, strokeDasharray: "4 2" },
    });
  }

  return { nodes, edges };
}
