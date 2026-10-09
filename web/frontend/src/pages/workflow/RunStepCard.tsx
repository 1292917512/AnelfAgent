import { useTranslation } from "react-i18next";
import { CheckCircle2, Clock3, Loader2, XCircle } from "lucide-react";
import type { WorkflowNode, WorkflowStep } from "@/lib/api";
import { Badge } from "@/components/ui";

export function RunStepCard({ step, nodes, running }: { step: WorkflowStep; nodes: WorkflowNode[]; running: boolean }) {
  const { t } = useTranslation("workflow");
  const attempts = nodes.filter((node) => node.kind !== "repair");
  const latest = attempts[attempts.length - 1];
  const status = latest?.status ?? (running ? "pending" : "notRun");
  const Icon = status === "completed" ? CheckCircle2 : status === "failed" ? XCircle : status === "running" ? Loader2 : Clock3;
  const dependencies = [...new Set([...(step.depends_on ?? []), ...(step.continue_from ? [step.continue_from] : [])])];
  return <details className="group rounded-lg border border-border bg-background" open={status === "failed" || status === "running" || undefined}>
    <summary className="flex cursor-pointer list-none items-start gap-3 p-3">
      <Icon size={17} className={status === "completed" ? "mt-0.5 shrink-0 text-ok" : status === "failed" ? "mt-0.5 shrink-0 text-danger" : status === "running" ? "mt-0.5 shrink-0 animate-spin text-accent" : "mt-0.5 shrink-0 text-muted"} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{step.key}</span><Badge>{step.kind}</Badge>
          <span className="text-xs text-muted">{t(`nodeStatus.${status}`)}</span></div>
        <p className="mt-1 line-clamp-2 break-words text-xs text-muted">{step.goal || step.tool}</p>
        {!!dependencies.length && <p className="mt-2 break-words text-xs text-muted">{t("detail.dependencies")}: {dependencies.join(" · ")}</p>}
      </div>
      {!!nodes.length && <span className="shrink-0 text-xs text-muted">{t("detail.rounds", { count: nodes.length })}</span>}
    </summary>
    <div className="space-y-3 border-t border-border p-3">
      {nodes.map((node) => <section key={node.ordinal} className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <Badge>{node.kind} #{node.ordinal}</Badge><span>{t(`nodeStatus.${node.status}`)}</span>
          {node.delegation_id && <span className="break-all">{t("detail.delegation")}: {node.delegation_id}</span>}
        </div>
        {node.error && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-danger/5 p-3 text-xs text-danger">{node.error}</pre>}
        {node.result && <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-elevated p-3 text-sm">{node.result}</pre>}
        {node.usage && <details className="text-xs text-muted"><summary className="cursor-pointer">{t("detail.usage")}</summary>
          <pre className="mt-2 whitespace-pre-wrap break-words">{JSON.stringify(node.usage, null, 2)}</pre></details>}
      </section>)}
      {!nodes.length && <p className="text-xs text-muted">{t("detail.noSteps")}</p>}
    </div>
  </details>;
}
