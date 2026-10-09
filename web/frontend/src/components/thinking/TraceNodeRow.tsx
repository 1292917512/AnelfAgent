import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TraceNode } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TraceStatus } from "./TraceStatus";
import { durationLabel, nodeStage, nodeSummary, nodeTitle } from "./trace-model";

export function TraceNodeRow({ node, selected, onSelect, compact = false }: {
  node: TraceNode; selected: boolean; onSelect: () => void; compact?: boolean;
}) {
  const { t } = useTranslation("thinking");
  const summary = nodeSummary(node, t);
  return <button type="button" aria-pressed={selected} onClick={onSelect}
    data-trace-node={node.id}
    className={cn("group flex w-full min-w-0 items-start gap-3 rounded-lg border p-3 text-left transition-colors",
      selected ? "border-accent bg-accent-subtle" : node.status === "error" ? "border-danger/25 bg-danger-subtle/40" : "border-border bg-card hover:border-border-strong hover:bg-hover")}>
    <span className="mt-0.5"><TraceStatus status={node.status} /></span>
    <span className="min-w-0 flex-1">
      <span className="flex items-baseline justify-between gap-2">
        <span className="break-words text-xs font-medium text-heading">{nodeTitle(node, t)}</span>
        {node.duration_ms !== null && <span className="shrink-0 text-[11px] tabular-nums text-muted">{durationLabel(node.duration_ms)}</span>}
      </span>
      <span className="mt-1 block text-[10px] text-muted">{t(`stages.${nodeStage(node)}`)} · {t(`nodeTypes.${node.type}`, { defaultValue: node.type })}</span>
      {summary && <span className={cn("mt-1.5 block break-words text-xs leading-relaxed", compact ? "line-clamp-2" : "line-clamp-3", node.status === "error" ? "text-danger" : "text-muted")}>{summary}</span>}
    </span>
    <ChevronRight size={13} className="mt-1 shrink-0 text-muted" aria-hidden />
  </button>;
}
