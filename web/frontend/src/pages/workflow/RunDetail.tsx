import { useTranslation } from "react-i18next";
import type { WorkflowRunDetail } from "@/lib/api";
import { Badge } from "@/components/ui";
import { RunStepCard } from "./RunStepCard";

export function RunDetail({ detail }: { detail: WorkflowRunDetail }) {
  const { t, i18n } = useTranslation("workflow");
  const nodesByKey = new Map<string, typeof detail.nodes>();
  for (const node of detail.nodes) nodesByKey.set(node.key, [...(nodesByKey.get(node.key) ?? []), node]);
  const completed = detail.spec.steps.filter((step) => {
    const nodes = nodesByKey.get(step.key)?.filter((node) => node.kind !== "repair");
    return nodes?.[nodes.length - 1]?.status === "completed";
  }).length;
  return <div className="space-y-4">
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{t("detail.steps")}</h3>
        <span className="text-xs text-muted">{t("detail.progress", { completed, total: detail.spec.steps.length })}</span>
      </div>
      <progress className="mb-4 h-1.5 w-full overflow-hidden rounded-full border-0 bg-elevated [&::-webkit-progress-bar]:bg-elevated [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-accent [&::-moz-progress-bar]:bg-accent" max={detail.spec.steps.length || 1} value={completed}
        aria-label={t("detail.progress", { completed, total: detail.spec.steps.length })} />
      {detail.spec.description && <p className="mb-3 text-sm text-muted">{detail.spec.description}</p>}
      <div className="space-y-3">{detail.spec.steps.map((step) =>
        <RunStepCard key={step.key} step={step} nodes={nodesByKey.get(step.key) ?? []} running={!!detail.run.running} />)}</div>
    </div>
    <details className="rounded-xl border border-border bg-card p-4">
      <summary className="cursor-pointer text-sm font-medium">{t("detail.timeline")} · {detail.events.length}</summary>
      {detail.events_truncated && <p className="mt-3 text-xs text-muted">{t("detail.eventsTruncated", { count: detail.events.length })}</p>}
      <div className="mt-3 max-h-96 space-y-2 overflow-auto">
        {[...detail.events].reverse().map((event) => <details key={event.sequence} className="rounded-lg bg-elevated p-3">
          <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-xs">
            <span className="text-muted">#{event.sequence}</span><Badge>{event.type}</Badge>
            {typeof event.payload.key === "string" && <span>{event.payload.key}</span>}
            <time className="ml-auto text-muted">{new Date(event.ts * 1000).toLocaleTimeString(i18n.language)}</time>
          </summary>
          <pre className="mt-2 whitespace-pre-wrap break-words text-xs">{JSON.stringify(event.payload, null, 2)}</pre>
        </details>)}
        {!detail.events.length && <p className="text-xs text-muted">{t("detail.noEvents")}</p>}
      </div>
    </details>
  </div>;
}
