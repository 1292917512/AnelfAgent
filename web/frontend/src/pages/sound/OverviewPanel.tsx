import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { InlineContributions } from "@/components/extensions/ContributionSlot";
import { audioApi } from "@/lib/api";
import { Badge, Button } from "@/components/ui";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { AudioIntakePanel } from "./AudioIntakePanel";
import { RecordingsPanel } from "./RecordingsPanel";

export function AudioOverviewPanel() {
  const { t } = useTranslation("sound");
  const query = useQuery({
    queryKey: ["audioStatus"], queryFn: () => audioApi.status().then((r) => r.data),
    refetchInterval: 8000, throwOnError: false,
  });
  const status = query.data;
  const library = status?.library ?? {};
  const providers = [...(status?.providers ?? []), ...(status?.tts_providers ?? []).map((p) => ({ ...p, kind: "tts" }))];
  return <AsyncState pending={query.isPending} error={!status && query.error} retry={() => void query.refetch()}>
    {status && <div className="space-y-4">
      {query.isError && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          { name: "asr", ready: status.asr_available },
          { name: "voiceprint", ready: status.voiceprint_available },
          { name: "tts", ready: status.tts_available },
        ].map((cap) => <Card key={cap.name} title={t(cap.name)} className="!p-4">
          <Badge variant={cap.ready ? "ok" : "neutral"}>{t(cap.ready ? "ready" : "unavailable")}</Badge>
        </Card>)}
      </div>
      <div className="grid items-start gap-4 xl:grid-cols-2">
        <div className="space-y-4">
        <Card title={t("providersTitle")} subtitle={t("providersHint")} actions={
          <Button size="icon" variant="ghost" aria-label={t("common:refresh")} disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={15} /></Button>
        }>
          <div className="divide-y divide-border">
            {providers.map((p) => <div key={p.kind + "/" + p.name} className="py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className={"size-2 shrink-0 rounded-full " + (p.available ? "bg-ok" : "bg-muted")} />
                <span className="min-w-0 flex-1 break-words text-sm font-medium text-heading">{p.name}</span>
                <span className="text-xs text-muted">{t("kind." + p.kind, { defaultValue: p.kind })}</span>
                <Badge variant={p.available ? "ok" : p.state === "error" ? "danger" : "neutral"}>{t("providerState." + (p.state ?? (p.available ? "ready" : "unavailable")))}</Badge>
              </div>
              {p.hint && <p className="mt-2 break-words text-xs leading-relaxed text-muted">{p.hint}</p>}
            </div>)}
          </div>
          {!providers.length && <p className="text-sm text-muted">{t("noProviders")}</p>}
        </Card>
        <AudioIntakePanel available={status.asr_available} />
        </div>
        <div className="space-y-4">
          <Card title={t("libraryStats")}>
            <dl className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3">
              {[
                ["segments", library.segments], ["speakers", library.speakers], ["stats.pending", library.pending_speakers],
                ["stats.bound", library.bound_speakers], ["stats.unread", library.unread_segments],
                ["audioMinutes", Math.round((library.audio_ms ?? 0) / 6000) / 10],
              ].map(([label, value]) => <div key={label}>
                <dt className="text-xs text-muted">{t(String(label))}</dt><dd className="mt-1 text-xl font-semibold tabular-nums text-heading">{value ?? 0}</dd>
              </div>)}
            </dl>
            {!!library.voiceprint_dims && <p className="mt-4 text-xs text-muted">{t("stats.vpDims")}: {library.voiceprint_dims}</p>}
            {library.db_path && <p className="mt-3 break-all font-mono text-xs text-muted">{library.db_path}</p>}
          </Card>
          <Card title={t("injection.title")} actions={<Badge variant={status.injection.active ? "ok" : "neutral"}>{t(status.injection.active ? "injection.active" : "injection.inactive")}</Badge>}>
            <p className="text-xs leading-relaxed text-muted">{t("injection.hint")}</p>
            <div className="mt-4 border-t border-border pt-4">
              <div className="flex flex-wrap items-center gap-2 text-sm text-heading">
                <span className="flex-1">{t("realtimeStatus")}</span>
                <Badge variant={status.realtime.enabled ? "accent" : "neutral"}>{status.realtime.mode}</Badge>
                <span className="text-xs text-muted">{t("realtimeSessions", { count: status.realtime.sessions })}</span>
              </div>
              <div className="mt-2 space-y-1">
                {Object.entries(status.realtime.states).map(([owner, state]) => <p key={owner} className="flex flex-wrap justify-between gap-2 text-xs text-muted"><span className="break-all">{owner}</span><span>{state}</span></p>)}
              </div>
            </div>
          </Card>
        </div>
      </div>
      <InlineContributions slot="audio.overview.cards" componentProps={{}} />
      <RecordingsPanel />
    </div>}
  </AsyncState>;
}
