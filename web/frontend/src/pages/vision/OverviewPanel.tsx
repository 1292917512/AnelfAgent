import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, MonitorPlay, Play, Radio, RefreshCw, Square } from "lucide-react";
import { apiErrorMessage, visionApi } from "@/lib/api";
import { formatAge } from "@/lib/utils";
import { Badge, Button, Select, Switch } from "@/components/ui";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";

export function VisionOverviewPanel() {
  const { t } = useTranslation("vision");
  const client = useQueryClient();
  const [source, setSource] = useState("");
  const [revision, setRevision] = useState(0);
  const [failedUrl, setFailedUrl] = useState("");
  const query = useQuery({ queryKey: ["visionStatus"], queryFn: () => visionApi.status().then((r) => r.data), refetchInterval: 5000, throwOnError: false });
  const sources = useQuery({ queryKey: ["visionSources"], queryFn: () => visionApi.sources().then((r) => r.data.sources), throwOnError: false });
  const watch = useMutation({
    mutationFn: (data: { action: string; source: string }) => visionApi.watch(data.action, data.source),
    onSuccess: async () => { await Promise.all([client.invalidateQueries({ queryKey: ["visionStatus"] }), client.invalidateQueries({ queryKey: ["visionSources"] })]); },
  });
  const status = query.data;
  const frame = source ? status?.frames[source] : status?.latest;
  const url = frame ? visionApi.latestUrl(frame.source, `${frame.captured_at}-${revision}`) : "";
  const injection = status?.injection;
  return <AsyncState pending={query.isPending} error={!status && query.error} retry={() => void query.refetch()}>
    {status && <div className="space-y-4">
      {query.isError && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(260px,1fr)]">
        <Card title={t("latestFrame")} actions={<Button size="icon" variant="ghost" title={t("refresh")} onClick={() => { setRevision((n) => n + 1); void query.refetch(); }}><RefreshCw size={15} /></Button>}>
          <Select className="mb-3 w-full" aria-label={t("previewSource")} value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="">{t("followLatest")}</option>
            {[...new Set([...(sources.data ?? []).map((s) => s.key), ...Object.keys(status.frames), ...(source ? [source] : [])])].map((key) =>
              <option key={key} value={key}>{sources.data?.find((s) => s.key === key)?.display_name ?? key}</option>)}
          </Select>
          <div className="grid min-h-52 place-items-center overflow-hidden rounded-lg border border-border bg-secondary sm:min-h-72">
            {frame && failedUrl !== url ? <img src={url} alt={t("latestFrame")} onError={() => setFailedUrl(url)}
              className="max-h-[26rem] w-full object-contain" /> : <div className="max-w-sm space-y-3 p-6 text-center text-sm text-muted">
              <Camera size={28} className="mx-auto opacity-50" /><p>{t(frame ? "previewUnavailable" : "noFrameHint")}</p>
            </div>}
          </div>
          {frame && <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
            <span className="break-all">{frame.source}</span><span className="tabular-nums">{frame.width > 0 && `${frame.width} × ${frame.height} · `}{formatAge(frame.captured_at)}</span>
          </div>}
        </Card>
        <div className="space-y-4">
          <Card title={t("perceptionStatus")}>
            <dl className="space-y-3 text-sm">
              <div className="flex items-center justify-between gap-3"><dt className="text-muted">{t("sources")}</dt><dd className="tabular-nums text-heading">{sources.data?.length ?? "—"}</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-muted">{t("watching")}</dt><dd className="tabular-nums text-heading">{status.watching.length}</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-muted">{t("lastChange")}</dt><dd className="text-heading">{status.last_change_at ? formatAge(status.last_change_at) : "—"}</dd></div>
            </dl>
          </Card>
          <Card title={t("injection.title")} actions={<Radio size={16} className="text-accent" />}>
            <Badge variant={injection?.active ? "ok" : "neutral"}>{t(injection?.active ? "injection.active" : "injection.inactive")}</Badge>
            <p className="mt-3 text-xs leading-relaxed text-muted">{t("injection.hint")}</p>
            {injection?.last_media_inject && <p className="mt-3 break-words text-xs text-muted">{t("injection.lastMedia")}: {injection.last_media_inject.source} · {formatAge(injection.last_media_inject.at)}</p>}
            {injection?.last_text_inject_at ? <p className="mt-2 text-xs text-muted">{t("injection.lastText")}: {formatAge(injection.last_text_inject_at)}</p> : null}
          </Card>
        </div>
      </div>
      <h2 className="text-sm font-semibold text-heading">{t("sources")}</h2>
      {sources.isError && <QueryError compact error={sources.error} retry={() => void sources.refetch()} />}
      {watch.isError && <p role="alert" className="text-sm text-danger">{apiErrorMessage(watch.error, t("common:requestFailed"))}</p>}
      <div className="grid items-start gap-3 lg:grid-cols-2">
        {sources.data?.map((item) => {
          const st = status.sources[item.key];
          const watching = status.watching.includes(item.key);
          return <Card key={item.key} title={item.display_name} subtitle={item.description}
            actions={<Switch label={t("toggleSource", { name: item.display_name })} checked={item.enabled} disabled={watch.isPending}
              onChange={(enabled) => watch.mutate({ action: enabled ? "enable" : "disable", source: item.key })} />}>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <MonitorPlay size={14} /><code className="break-all">{item.key}</code>
              <Badge variant={watching ? "ok" : "neutral"}>{t(!item.enabled ? "sourceDisabled" : watching ? "watching" : "idle")}</Badge>
              {item.pollable && <Button size="sm" className="ml-auto" disabled={!item.enabled || watch.isPending}
                onClick={() => watch.mutate({ action: watching ? "stop" : "start", source: item.key })}>
                {watching ? <Square size={12} /> : <Play size={12} />}{t(watching ? "stopWatch" : "startWatch")}
              </Button>}
              {!item.pollable && <span className="ml-auto">{t("externalSource")}</span>}
            </div>
            <p className="mt-3 text-xs text-muted">{t("captures", { count: st?.capture_count ?? 0 })}{st?.last_capture_at ? ` · ${formatAge(st.last_capture_at)}` : ""}</p>
            {st?.last_error && <p className="mt-2 break-words text-xs text-danger">{st.last_error}</p>}
          </Card>;
        })}
      </div>
      {sources.data?.length === 0 && <p className="rounded-xl border border-dashed border-border p-6 text-sm text-muted">{t("noSources")}</p>}
    </div>}
  </AsyncState>;
}
