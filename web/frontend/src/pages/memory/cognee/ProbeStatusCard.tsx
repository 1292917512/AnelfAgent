import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { Spinner } from "@/components/ui";
import { memoryApi } from "@/lib/api";

export function ProbeStatusCard() {
  const { t } = useTranslation("memory");
  const { data: probe, error, refetch } = useQuery({
    queryKey: ["memoryProbeStatus"],
    queryFn: () => memoryApi.cognee.probeStatus().then((r) => r.data),
    refetchInterval: 10000,
    throwOnError: false,
  });
  return (
    <Card title={t("cognee.probeTitle")} subtitle={t("cognee.probeSubtitle")}>
      {error && <QueryError error={error} retry={() => void refetch()} compact />}
      {probe ? (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            {!probe.enabled && (
              <span className="rounded bg-elevated border border-border px-2 py-0.5 text-xs text-danger">
                {t("cognee.probeDisabled")}
              </span>
            )}
            {probe.enabled && !probe.inject_enabled && (
              <span className="rounded bg-elevated border border-border px-2 py-0.5 text-xs text-warning">
                {t("cognee.probeInjectOff")}
              </span>
            )}
            <span className="text-xs text-muted">
              {t("cognee.probeStarted")} {probe.counters["probe.started"] ?? 0}
              {" · "}
              {t("cognee.probeCompleted")} {probe.counters["probe.completed"] ?? 0}
              {" · "}
              {t("cognee.probeInjected")} {probe.counters["probe.injected"] ?? 0}
              {" · "}
              {t("cognee.probeTimeout")} {probe.counters["probe.timeout"] ?? 0}
            </span>
          </div>
          {probe.scopes.length > 0 ? (
            <div className="space-y-1.5">
              {probe.scopes.map((s) => (
                <div key={s.scope} className="rounded-md border border-border bg-elevated p-2">
                  <div className="flex items-center gap-2 text-xs text-muted">
                    <span className="font-mono">{s.scope || "(global)"}</span>
                    {s.running && (
                      <span className="text-warning">{t("cognee.probeRunning")}</span>
                    )}
                    <span className="ml-auto">{s.rendered_chars} chars · {s.age_seconds}s</span>
                  </div>
                  {s.rendered_preview && (
                    <p className="mt-1 line-clamp-3 break-words text-xs text-heading whitespace-pre-wrap">
                      {s.rendered_preview}
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted">{t("cognee.probeNoActivity")}</p>
          )}
        </div>
      ) : (
        !error && <Spinner className="h-4 w-4" />
      )}
    </Card>
  );
}
