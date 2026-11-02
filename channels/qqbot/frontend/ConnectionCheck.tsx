import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Activity } from "lucide-react";
import { adaptersApi, apiErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui";
import { StatusDot } from "@/components/common/StatusDot";
import type { SetupInfo } from "./api";

export function ConnectionCheck({ setup, status, compact, dirty, busy }: {
  setup: SetupInfo; status: string; compact: boolean; dirty: boolean; busy: boolean;
}) {
  const { t, i18n } = useTranslation("channel-qqbot");
  const probe = useMutation({ mutationFn: async () => (await adaptersApi.testHealth("qqbot")).data });
  const result = probe.data;
  const running = status === "running";
  const error = probe.error ? apiErrorMessage(probe.error, t("probeFailed")) :
    result && !result.healthy ? result.last_error || result.error || result.health_detail || t("probeFailed") : null;
  return <section aria-label={t("verify")} className="space-y-2 rounded-lg border border-border bg-panel p-3">
    {!compact && <h4 className="flex items-center gap-2 text-sm font-semibold text-heading"><Activity size={15} />{t("verify")}</h4>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
        <div><dt className="sr-only">{t("runtime")}</dt><dd className="flex items-center gap-2 font-medium text-heading">
          {compact && <StatusDot status={running ? "ok" : "warn"} />}
          {t(`status.${status}`, { defaultValue: status })}{setup.mode && ` · ${t(`mode.${setup.mode}`)}`}
        </dd></div>
        <div className="flex flex-wrap gap-x-1.5 text-muted"><dt className={setup.last_event_at ? undefined : "sr-only"}>{t("lastEvent")}</dt><dd>
          {setup.last_event_at ? new Date(setup.last_event_at * 1000).toLocaleString(i18n.language) : t("noEvent")}
        </dd></div>
      </dl>
      <Button size="sm" disabled={!running || dirty || busy} loading={probe.isPending} onClick={() => probe.mutate()}>{t("checkApi")}</Button>
    </div>
    {setup.last_error && <p role="alert" className="break-words text-xs text-danger">{setup.last_error}</p>}
    {!dirty && running && !busy && !probe.isPending && (result || probe.error) && <p role="status" className={error ? "text-xs text-danger" : "text-xs text-ok"}>
      {error ?? t("apiOk")}
    </p>}
    {(dirty || !compact) && <p className="text-xs leading-relaxed text-muted">{t(dirty ? "checkSaved" : "checkAfterStart")}</p>}
  </section>;
}
