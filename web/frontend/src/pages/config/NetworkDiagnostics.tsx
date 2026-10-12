import { useState } from "react";
import { useIsMutating, useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Activity, ArrowRight, CheckCircle2, CircleAlert } from "lucide-react";
import { networkApi } from "@/lib/api/network";
import { apiErrorMessage } from "@/lib/api/client";
import type { ProbeRouteReport } from "@/lib/types/network";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/utils";

function RouteResult({ route }: { route: ProbeRouteReport }) {
  const { t } = useTranslation("config");
  const passed = route.status === "passed";
  const source = (value: string) => value.startsWith("rule:")
    ? t("networkCheck.rule", { index: value.slice(5) })
    : t(`networkCheck.sources.${value}`, { defaultValue: value || "—" });
  return <div className="min-w-0 rounded-xl border border-border bg-card p-3.5">
    <div className="flex items-center justify-between gap-2">
      <h4 className="text-sm font-medium">{t(`networkCheck.modes.${route.mode}`)}</h4>
      {passed ? <CheckCircle2 size={16} className="text-ok" /> : <CircleAlert size={16} className="text-warn" />}
    </div>
    <p className={cn("mt-2 text-sm", passed ? "text-ok" : "text-warn")}>{t(`networkCheck.status.${route.status}`)}</p>
    {route.samples.length > 0 && <>
      <p className="mt-1 text-xs text-muted">{t("networkCheck.reached", { count: route.reachable_count, total: route.samples.length })}</p>
      <div className="my-3 flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
        <span>{t("networkCheck.median")} <strong>{route.median_ms === null ? "—" : `${route.median_ms} ms`}</strong></span>
        <span>{t("networkCheck.spread")} <strong>{route.spread_ms === null ? "—" : `${route.spread_ms} ms`}</strong></span>
      </div>
      <ol className="space-y-2 border-t border-border pt-3">
        {route.samples.map((sample) => <li key={sample.index} className="text-xs">
          <div className="flex items-center gap-1.5 tabular-nums">
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", sample.http_ok ? "bg-ok" : "bg-warn")} />
            <span className="text-muted">#{sample.index}</span>
            {sample.attempts > 1 && <><span>{t("networkCheck.modes.direct")}</span><ArrowRight size={11} /></>}
            <span>{sample.path ? t(`networkCheck.modes.${sample.path}`) : "—"}</span>
            <span className="ml-auto whitespace-nowrap">{sample.elapsed_ms} ms</span>
          </div>
          <p className="mt-1 break-words pl-3 text-muted">
            {source(sample.source)}{sample.status_code !== null && ` · HTTP ${sample.status_code}`}
          </p>
          {sample.error_code && <p className="mt-1 break-words pl-3 text-warn">{t(`networkCheck.errors.${sample.error_code}`)}</p>}
        </li>)}
      </ol>
    </>}
    {route.error_code && <p className="mt-2 text-xs leading-relaxed text-muted">{t(`networkCheck.errors.${route.error_code}`)}</p>}
  </div>;
}

export function NetworkDiagnostics() {
  const { t } = useTranslation("config");
  const [url, setUrl] = useState("https://www.gstatic.com/generate_204");
  const [samples, setSamples] = useState(3);
  const saving = useIsMutating({ predicate: (entry) => entry.options.scope?.id.startsWith("config:") ?? false }) > 0;
  const mutation = useMutation({ mutationFn: () => networkApi.diagnose({ url, samples, mode: "compare", timeout_seconds: 5 }).then((r) => r.data) });
  const report = mutation.data;
  return <section aria-label={t("networkCheck.title")} className="rounded-xl border border-border bg-elevated/40 p-4 sm:p-5">
    <div className="flex items-center gap-2"><Activity size={17} className="text-accent" /><h3 className="font-medium">{t("networkCheck.title")}</h3></div>
    <p className="mt-2 text-xs leading-relaxed text-muted">{t("networkCheck.description")}</p>
    <form className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-end" onSubmit={(e) => { e.preventDefault(); if (!saving && !mutation.isPending) mutation.mutate(); }}>
      <label className="min-w-0 flex-1 space-y-1.5 text-xs text-muted">
        <span>{t("networkCheck.url")}</span>
        <Input type="url" required value={url} onChange={(e) => setUrl(e.target.value)} disabled={mutation.isPending} />
      </label>
      <div className="flex items-end gap-2">
        <label className="flex flex-1 flex-col gap-1.5 text-xs text-muted">
          <span>{t("networkCheck.samples")}</span>
          <Select value={samples} disabled={mutation.isPending} onChange={(e) => setSamples(Number(e.target.value))}>
            {[1, 3, 5].map((count) => <option key={count} value={count}>{t("networkCheck.sampleCount", { count })}</option>)}
          </Select>
        </label>
        <Button type="submit" variant="primary" disabled={saving} loading={mutation.isPending}>{t(mutation.isPending ? "networkCheck.running" : "networkCheck.start")}</Button>
      </div>
    </form>
    {mutation.isPending && <p role="status" className="mt-3 text-xs text-muted">{t("networkCheck.progress", { seconds: samples * 5 + 1 })}</p>}
    {mutation.error && <p role="alert" className="mt-3 text-sm text-danger">{apiErrorMessage(mutation.error, t("networkCheck.failed"))}</p>}
    {report && !mutation.isPending && <div className="mt-4 space-y-3" aria-live="polite">
      <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
        <span className="break-all">{report.url}</span>
        <span>{new Date(report.checked_at * 1000).toLocaleTimeString()} · {(report.elapsed_ms / 1000).toFixed(1)} s</span>
      </div>
      {report.configuration_changed && <p role="alert" className="text-xs text-warn">{t("networkCheck.changed")}</p>}
      <div className="grid gap-3 xl:grid-cols-3">{report.routes.map((route) => <RouteResult key={route.mode} route={route} />)}</div>
      <p className="text-xs leading-relaxed text-muted">{t("networkCheck.limitation")}</p>
    </div>}
  </section>;
}
