import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui";
import { api, apiErrorMessage } from "@/lib/api/client";
import type { GitHubStatus } from "./types";

export function StatusPanel() {
  const { t } = useTranslation("channel-github");
  const status = useQuery({ queryKey: ["github-status"], queryFn: async () => (await api.get<GitHubStatus>("/channels/github/status")).data, refetchInterval: 10000 });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const retry = async (event_id = "", repo = "") => {
    setBusy(true); setError("");
    try { await api.post("/channels/github/retry", { event_id, repo }, { errorFeedback: "local" }); await status.refetch(); }
    catch (cause) { setError(apiErrorMessage(cause, t("failed"))); }
    finally { setBusy(false); }
  };
  const data = status.data;
  return <section className="space-y-3" aria-label={t("status.title")}>
    <div className="flex flex-wrap gap-2 text-xs text-muted">
      <span>{t("status.runtime")}: {data?.status ?? t("status.stopped")}</span>
      <span>{t("status.pending")}: {(data?.inbox?.counts.pending ?? 0) + (data?.inbox?.counts.processing ?? 0)}</span>
      <span>{t("status.failed")}: {data?.inbox?.counts.failed ?? 0}</span>
      <span>{t("status.sent")}: {data?.dispatched_events ?? 0}</span>
      <span>{t("status.quota")}: {data?.rate_remaining ?? "—"}</span>
    </div>
    <div className="flex flex-wrap gap-2">
      <Button size="sm" onClick={() => void status.refetch()} loading={status.isFetching}>{t("refresh")}</Button>
      <Button size="sm" onClick={() => void retry()} disabled={!data?.enabled || busy}>{t("retryAll")}</Button>
    </div>
    {(error || status.error) && <p role="alert" className="text-xs text-danger">{error || apiErrorMessage(status.error, t("failed"))}</p>}
    {Object.entries(data?.repo_states ?? {}).filter(([, state]) => state.last_error || state.possible_gap).map(([repo, state]) =>
      <div key={repo} className="flex flex-wrap items-center gap-2 rounded-lg border border-border p-3 text-xs">
        <span className="font-medium">{repo}</span><span className="break-all text-danger">{state.last_error || t("status.gap")}</span>
        <Button size="sm" disabled={busy} onClick={() => void retry("", repo)}>{t("resume")}</Button>
      </div>)}
    {data?.inbox?.events.map(event => <div key={event.id} className="space-y-1 rounded-lg border border-border p-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="break-all font-mono">{event.id.slice(0, 16)}</span>
        <span>{t(`state.${event.state}`, { defaultValue: event.state })} · {t("attempts", { count: event.attempts })}</span>
        {event.state === "failed" && <Button size="sm" disabled={busy} onClick={() => void retry(event.id)}>{t("retry")}</Button>}
      </div>
      {event.error && <p className="break-all text-danger">{event.error}</p>}
    </div>)}
  </section>;
}
