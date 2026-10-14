import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, Brain, Download, RefreshCw, Terminal, Timer, Send, Workflow } from "lucide-react";
import { contextApi } from "@/lib/api";
import type { DiagnosticStep } from "@/lib/types";
import { cn, formatDurationMs } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";
import { downloadJson } from "./downloadJson";

const icons = { model: Brain, tool: Terminal, context: Timer, hook: Workflow, delivery: Send };

function cacheRate(step: DiagnosticStep): string {
  const usage = step.usage;
  if (usage?.cache_observable !== true || !usage.total_input_tokens || usage.cache_read_input_tokens == null) return "—";
  return `${Math.round(Math.min(1, usage.cache_read_input_tokens / usage.total_input_tokens) * 100)}%`;
}

export function DiagnosticsTab() {
  const { t } = useTranslation("context");
  const [scope, setScope] = useState("");
  const [auto, setAuto] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ run: string; step: string; kind: DiagnosticStep["kind"] } | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const query = useQuery({
    queryKey: ["conversation-diagnostics", scope, expanded],
    queryFn: () => contextApi.recent(scope, expanded ?? "", expanded ? 80 : 12).then((r) => r.data),
    refetchInterval: auto ? 5000 : false,
    refetchIntervalInBackground: false,
    throwOnError: false,
  });
  const data = query.data;
  const selectedRun = data?.runs.find((run) => run.id === selected?.run);
  const detail = selectedRun?.steps.find((step) => step.id === selected?.step && step.kind === selected.kind)
    ?? selectedRun?.active_steps.find((step) => step.id === selected?.step && step.kind === selected.kind);
  const status = (value: string) => t(`diagnostics.status.${value}`, { defaultValue: value });

  return <section className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <select aria-label={t("diagnostics.scope")} value={scope} onChange={(event) => { setScope(event.target.value); setExpanded(null); setDetailOpen(false); }}
        className="min-w-0 max-w-full flex-1 rounded-lg border border-border bg-card p-2 text-sm">
        <option value="">{t("diagnostics.all")}</option>
        {Array.from(new Set([...(data?.scopes ?? []), ...(scope ? [scope] : [])])).filter(Boolean).map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
      <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />{t("diagnostics.auto")}</label>
      <Button size="icon" title={t("diagnostics.refresh")} onClick={() => void query.refetch()} disabled={query.isFetching}><RefreshCw size={15} /></Button>
      <Button size="icon" title={t("monitor.export")} disabled={!data} onClick={() => data && downloadJson(data, "conversation-diagnostics.json")}><Download size={15} /></Button>
    </div>
    <p className="text-xs leading-relaxed text-muted">{t("diagnostics.description")}</p>
    <code className="block break-all text-xs text-muted">GET /api/context/recent · get_conversation_diagnostics</code>
    {query.isPending && <PageSkeleton />}
    {query.error && <QueryError error={query.error} retry={() => void query.refetch()} />}
    {data && <>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span>{t(data.collecting ? "diagnostics.collecting" : "diagnostics.stopped")}</span>
        <span>{t("diagnostics.observed", { time: new Date(data.observed_at * 1000).toLocaleTimeString() })}</span>
        <span>{t("diagnostics.retention", { count: data.retention.runs })}</span>
      </div>
      {expanded && <Button size="sm" onClick={() => { setExpanded(null); setDetailOpen(false); }}>{t("diagnostics.back")}</Button>}
      {!data.runs.length && <p className="py-8 text-center text-sm text-muted">{t("diagnostics.empty")}</p>}
      {data.runs.map((run) => {
        const visible = new Set(run.steps.map((step) => `${step.kind}:${step.id}`));
        const rows = [...run.active_steps.filter((step) => !visible.has(`${step.kind}:${step.id}`)), ...run.steps];
        return <article key={run.id} className="overflow-hidden rounded-xl border border-border bg-card">
          <header className="flex items-start gap-3 border-b border-border px-4 py-3">
            <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", run.status === "running" ? "bg-accent" : run.status === "failed" ? "bg-warn" : "bg-ok")} />
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex flex-wrap gap-2 text-xs text-muted"><span>{t(`diagnostics.kind.${run.kind}`, { defaultValue: run.kind })} · {status(run.status)}</span><span className="break-all">{run.origin_scope || run.scope}</span></div>
              <h3 className="line-clamp-2 break-words text-sm font-medium text-heading">{run.label || run.trigger || run.id}</h3>
            </div>
            <span className="shrink-0 text-xs tabular-nums text-muted" title={t("diagnostics.wallTime")}>{formatDurationMs(run.duration_ms)}</span>
          </header>
          <div className="divide-y divide-border/60">
            {rows.map((step) => {
              const Icon = icons[step.kind];
              return <button key={`${step.kind}:${step.id}`} onClick={() => { setSelected({ run: run.id, step: step.id, kind: step.kind }); setDetailOpen(true); }}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-xs transition-colors hover:bg-hover">
                <Icon size={14} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate font-mono">{["context", "delivery"].includes(step.kind) ? t(`diagnostics.kind.${step.kind}`) : step.name}</span>
                {step.kind === "model" && <span className="hidden shrink-0 text-muted sm:inline">{t("diagnostics.cache")} {cacheRate(step)}</span>}
                <span className={cn("shrink-0", ["failed", "error"].includes(step.status) ? "text-warn" : "text-muted")}>{status(step.status)}</span>
                <span className="w-14 shrink-0 text-right tabular-nums text-muted">{formatDurationMs(step.duration_ms)}</span>
                <ArrowUpRight size={12} className="shrink-0 text-muted" />
              </button>;
            })}
          </div>
          {(run.omitted_steps > 0 || run.error) && <footer className="border-t border-border px-4 py-2 text-xs text-muted">
            {run.error && <p className="break-words text-warn">{run.error}</p>}
            {run.omitted_steps > 0 && (expanded === run.id ? <p>{t("diagnostics.evictedSteps", { count: run.omitted_steps })}</p> : <button className="py-1 text-accent" onClick={() => setExpanded(run.id)}>{t("diagnostics.omitted", { count: run.omitted_steps })}</button>)}
          </footer>}
        </article>;
      })}
      {data.conversations.map((conversation) => <details key={conversation.scope} className="rounded-xl border border-border bg-card px-4 py-3">
        <summary className="cursor-pointer break-all text-sm">{t("diagnostics.messages")} · {conversation.scope} · {conversation.messages.length}</summary>
        {!conversation.loaded && <p className="mt-3 text-xs text-muted">{t("diagnostics.cold")}</p>}
        <ol className="mt-3 divide-y divide-border">
          {conversation.messages.map((message) => <li key={message.id} className="py-3">
            <div className="mb-1 flex gap-3 text-xs text-muted"><span>{message.role}</span><span>#{message.id}</span><time>{new Date(message.ts * 1000).toLocaleTimeString()}</time></div>
            <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{message.preview}</p>
          </li>)}
        </ol>
      </details>)}
    </>}
    <Modal open={detailOpen} onClose={() => setDetailOpen(false)} title={detail?.name ?? t("diagnostics.details")} width="max-w-2xl">
      {detail ? <div className="space-y-4 text-sm">
        <div className="flex flex-wrap gap-3 text-muted"><span>{status(detail.status)}</span><span>{formatDurationMs(detail.duration_ms)}</span></div>
        {detail.operation && <p className="break-words">{detail.operation}</p>}
        {detail.kind === "model" && <dl className="grid grid-cols-2 gap-3 rounded-lg bg-elevated p-3 text-xs">
          <dt>{t("diagnostics.ttft")}</dt><dd>{detail.ttft_ms == null ? "—" : formatDurationMs(detail.ttft_ms)}</dd>
          <dt>{t("diagnostics.cache")}</dt><dd>{cacheRate(detail)}</dd>
          <dt>{t("diagnostics.input")}</dt><dd>{detail.usage?.total_input_tokens?.toLocaleString() ?? "—"}</dd>
          <dt>{t("diagnostics.output")}</dt><dd>{detail.usage?.completion_tokens?.toLocaleString() ?? "—"}</dd>
        </dl>}
        {detail.delivery && <p className="break-all text-accent">{t("diagnostics.delivery")} · {status(detail.delivery.status ?? "unknown")} · {detail.delivery.scope}</p>}
        {[["arguments", detail.arguments], ["preview", detail.preview], ["error", detail.error]].map(([key, value]) => value && <div key={key}>
          <h4 className="mb-2 text-xs text-muted">{t(`diagnostics.${key}`)}</h4><pre className="whitespace-pre-wrap break-words rounded-lg bg-elevated p-3 text-xs leading-relaxed">{value}</pre>
        </div>)}
        <p className="text-xs leading-relaxed text-muted">{t(detail.kind === "model" ? "diagnostics.modelNote" : "diagnostics.excerptNote")}</p>
        <div className="space-y-1 break-all font-mono text-xs text-muted"><div>{selected?.run}</div><div>{detail.id}</div><div>{detail.request_id}</div></div>
      </div> : <p className="text-sm text-muted">{t("diagnostics.evicted")}</p>}
    </Modal>
  </section>;
}
