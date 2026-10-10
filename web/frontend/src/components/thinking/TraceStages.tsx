import { useTranslation } from "react-i18next";
import type { ThinkingSession } from "@/lib/types";
import { cn } from "@/lib/utils";
import { nodeStage, type TraceStage } from "./trace-model";

const STAGES: TraceStage[] = ["prepare", "reason", "act", "finish"];

export function TraceStages({ session, onSelect }: { session: ThinkingSession; onSelect: (id: string) => void }) {
  const { t } = useTranslation("thinking");
  return <div className="mt-3 grid grid-cols-4 gap-1 border-t border-border pt-3">
    {STAGES.map((stage, index) => {
      const nodes = session.nodes.filter((node) => nodeStage(node) === stage);
      const running = !session.ended && nodes.some((node) => node.status === "running");
      const issue = nodes.find((node) => node.status === "error" || node.status === "warning");
      const target = issue ?? nodes.find((node) => node.status === "running") ?? nodes[0];
      return <button key={stage} disabled={!target} onClick={() => { if (target) onSelect(target.id); }}
        title={t("nNodes", { count: nodes.length })}
        className={cn("flex min-w-0 items-center gap-2 rounded-lg p-2 text-left text-xs transition-colors", issue ? "bg-warn-subtle text-warn" : running ? "bg-accent-subtle text-accent" : "text-muted hover:bg-elevated", !target && "opacity-45")}>
        <span className={cn("flex size-5 shrink-0 items-center justify-center rounded-full border text-[10px] tabular-nums", running ? "border-accent" : "border-border")}>{index + 1}</span>
        <span className="truncate">{t(`stages.${stage}`)}</span>
        {running && <span className="ml-auto size-1.5 shrink-0 rounded-full bg-accent" />}
      </button>;
    })}
  </div>;
}
