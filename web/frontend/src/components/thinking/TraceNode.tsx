import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Brain, Layers, Wrench, Flag } from "lucide-react";
import type { TraceNode } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TraceStatus } from "./TraceStatus";
import { durationLabel, nodeStage, nodeSummary, nodeTitle, nodeUsage } from "./trace-model";

export type TraceGraphNode = Node<{ trace: TraceNode }, "trace">;
const ICONS = { prepare: Layers, reason: Brain, act: Wrench, finish: Flag };

function TraceNodeComponent({ data: { trace }, selected }: NodeProps<TraceGraphNode>) {
  const { t } = useTranslation("thinking");
  const Icon = ICONS[nodeStage(trace)];
  const summary = nodeSummary(trace, t);
  const tokens = nodeUsage(trace).total_tokens;
  return <>
    <Handle type="target" position={Position.Top} className="!bg-[var(--border-strong)]" />
    <div className={cn("w-[260px] rounded-xl border bg-card p-3 shadow-sm",
      trace.status === "error" ? "border-danger" : trace.status === "running" ? "border-accent" : "border-border",
      selected && "ring-2 ring-accent ring-offset-2 ring-offset-bg")}>
      <div className="mb-2 flex items-center justify-between gap-2 text-[10px] text-muted">
        <span className="flex items-center gap-1.5"><Icon size={12} />{t(`nodeTypes.${trace.type}`, { defaultValue: trace.type })}</span>
        <TraceStatus status={trace.status} />
      </div>
      <div className="truncate text-xs font-semibold text-heading">{nodeTitle(trace, t)}</div>
      {summary && <p className="mt-1.5 line-clamp-2 break-words text-[11px] leading-relaxed text-muted">{summary}</p>}
      <div className="mt-2 flex gap-3 text-[10px] tabular-nums text-muted">
        {trace.duration_ms !== null && <span>{durationLabel(trace.duration_ms)}</span>}
        {tokens > 0 && <span>{tokens.toLocaleString()} tokens</span>}
      </div>
    </div>
    <Handle type="source" position={Position.Bottom} className="!bg-[var(--border-strong)]" />
  </>;
}
export default memo(TraceNodeComponent);
