/** 关系图谱自定义节点：按类型着色，大小随连接度。 */

import { memo } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { cn } from "@/lib/utils";

export interface GraphNodeData extends Record<string, unknown> {
  label: string;
  nodeKey: string;
  nodeType: string;
  degree: number;
  typeLabel: string;
}

export type GraphFlowNode = Node<GraphNodeData, "graphNode">;

/** 节点类型 → 配色（bg/border/text 三元组，对齐全局设计令牌色系） */
export const NODE_TYPE_COLORS: Record<string, { bg: string; border: string }> = {
  user: { bg: "bg-blue-500/15", border: "border-blue-500/60" },
  group: { bg: "bg-purple-500/15", border: "border-purple-500/60" },
  person: { bg: "bg-amber-500/15", border: "border-amber-500/60" },
  topic: { bg: "bg-emerald-500/15", border: "border-emerald-500/60" },
  project: { bg: "bg-cyan-500/15", border: "border-cyan-500/60" },
  org: { bg: "bg-rose-500/15", border: "border-rose-500/60" },
  thing: { bg: "bg-lime-500/15", border: "border-lime-500/60" },
  concept: { bg: "bg-teal-500/15", border: "border-teal-500/60" },
  custom: { bg: "bg-slate-500/15", border: "border-slate-500/60" },
};

const FALLBACK_COLOR = { bg: "bg-slate-500/15", border: "border-slate-500/60" };

function GraphNodeCardInner({ data, selected }: NodeProps<GraphFlowNode>) {
  const colors = NODE_TYPE_COLORS[data.nodeType] ?? FALLBACK_COLOR;
  const size = Math.min(56, 40 + data.degree * 3);
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-full border-2 px-2 text-center shadow-sm transition-colors",
        colors.bg, colors.border,
        selected && "ring-2 ring-accent",
      )}
      style={{ width: size + 28, height: size + 28 }}
      title={data.nodeKey}
    >
      <Handle type="target" position={Position.Top} className="!opacity-0" />
      <div className="max-w-[90px] truncate text-xs font-medium text-heading">{data.label}</div>
      <div className="text-[10px] text-muted">{data.typeLabel}</div>
      <Handle type="source" position={Position.Bottom} className="!opacity-0" />
    </div>
  );
}

export const GraphNodeCard = memo(GraphNodeCardInner);
