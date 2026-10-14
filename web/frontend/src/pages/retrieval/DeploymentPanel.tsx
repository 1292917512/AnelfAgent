import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Download, Play, RefreshCw, Settings2, Square, Undo2, Unplug } from "lucide-react";
import { apiErrorMessage, retrievalApi } from "@/lib/api";
import type { RetrievalDeploymentAction } from "@/lib/types";
import { formatElapsedCompact } from "@/lib/format";
import { AsyncState } from "@/components/common/AsyncState";
import { Badge, Button, Input } from "@/components/ui";

const RegisteredConfigPanel = lazy(() => import("../config/RegisteredConfigPanel").then((module) => ({ default: module.RegisteredConfigPanel })));
const stateKey = ["retrievalDeployment"];

export function RetrievalDeploymentPanel() {
  const { t } = useTranslation("retrieval");
  const client = useQueryClient();
  const [images, setImages] = useState<Record<string, string>>({});
  const [showConfig, setShowConfig] = useState(false);
  const state = useQuery({
    queryKey: stateKey, queryFn: () => retrievalApi.deployment().then((r) => r.data), throwOnError: false,
    refetchInterval: (query) => query.state.data?.operation?.status === "running" ? 1500 : false,
  });
  const data = state.data;
  const running = data?.operation?.status === "running";
  const containers = useQuery({
    queryKey: ["retrievalContainers", data?.operation?.id, data?.operation?.status],
    queryFn: () => retrievalApi.deployment(true).then((r) => r.data), enabled: !!data && !running, throwOnError: false,
  });
  const action = useMutation({
    mutationFn: ({ name, versions }: { name: RetrievalDeploymentAction; versions?: Record<string, string> }) => retrievalApi.deploy(name, versions),
    onSuccess: (response) => {
      client.setQueryData(stateKey, response.data);
      void client.invalidateQueries({ queryKey: ["retrievalProvidersMatrix"] });
    },
  });
  const busy = running || action.isPending;
  const execute = (name: RetrievalDeploymentAction) => action.mutate({ name });
  return <AsyncState pending={state.isPending} error={!data && state.error} retry={() => void state.refetch()}>
    {data && <div className="space-y-5">
      <p className="text-sm text-muted">{t("deployment.scope")}</p>
      {!data.available && <div role="status" className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm">
        <p>{t("deployment.missing")}</p>
        <a className="mt-2 inline-block text-accent underline" href="https://docs.docker.com/engine/install/" target="_blank" rel="noreferrer">{t("deployment.install")}</a>
      </div>}
      <div className="divide-y divide-border rounded-xl border border-border px-3">
        {Object.entries(data.images).map(([name, version]) => {
          const container = containers.data?.containers?.find((item) => item.service === name);
          return <div key={name} className="flex flex-wrap items-start gap-2 py-3">
            <div className="min-w-0 flex-1"><p className="text-sm font-semibold text-heading">{name === "crawl4ai" ? "Crawl4AI" : "SearXNG"}</p>
              <p className="mt-1 break-all font-mono text-xs text-muted">{version}</p>
              <p className="mt-1 text-xs text-muted">127.0.0.1:{data.ports[name]}</p>
            </div>
            <Badge>{container ? `${container.state}${container.health ? ` · ${container.health}` : ""}` : t("deployment.unknown")}</Badge>
          </div>;
        })}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy || !data.available} onClick={() => execute("up")}><Play size={14} />{t("deployment.start")}</Button>
        <Button size="sm" disabled={busy || !data.available || !data.initialized} onClick={() => execute("stop")}><Square size={14} />{t("deployment.stop")}</Button>
        <Button size="sm" disabled={busy || !data.initialized} onClick={() => execute("connect")} title={t("deployment.connectHint")}><Unplug size={14} />{t("deployment.connect")}</Button>
        <Button size="sm" disabled={busy || !data.available || !data.rollback_available} onClick={() => execute("rollback")}><Undo2 size={14} />{t("deployment.rollback")}</Button>
        <Button size="sm" loading={state.isFetching || containers.isFetching} onClick={() => { void state.refetch(); void containers.refetch(); }}><RefreshCw size={14} />{t("deployment.refresh")}</Button>
      </div>
      <p className="text-xs text-muted">{t("deployment.connectHint")}</p>
      {data.operation && <div className="space-y-2 rounded-lg bg-elevated p-3 text-sm" aria-live="polite">
        <div className="flex flex-wrap gap-2"><span>{t(`deployment.status.${data.operation.status}`)}</span>{running && <span className="text-muted">{t(`deployment.phase.${data.operation.phase}`, { defaultValue: data.operation.phase })}</span>}</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums text-muted">
          {data.operation.elapsed_seconds != null && <span>{t("deployment.elapsed", { duration: formatElapsedCompact(data.operation.elapsed_seconds * 1000) })}</span>}
          {running && data.operation.phase_elapsed_seconds != null && <span>{t("deployment.phaseElapsed", { duration: formatElapsedCompact(data.operation.phase_elapsed_seconds * 1000) })}</span>}
          {running && data.operation.output_idle_seconds != null && <span>{t("deployment.lastOutput", { duration: formatElapsedCompact(data.operation.output_idle_seconds * 1000) })}</span>}
        </div>
        {running && <p className="text-xs text-muted">{t("deployment.backgroundHint")}</p>}
        {data.operation.error && <p role="alert" className="break-words text-danger">{data.operation.error}</p>}
        {data.operation.log && <details><summary className="cursor-pointer text-xs text-muted">{t("deployment.log")}</summary><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">{data.operation.log}</pre></details>}
      </div>}
      {(action.error || state.error || containers.error || containers.data?.error) && <p role="alert" className="break-words text-sm text-danger">{containers.data?.error || apiErrorMessage(action.error || state.error || containers.error, t("common:requestFailed"))}</p>}
      <details className="rounded-xl border border-border p-3">
        <summary className="cursor-pointer text-sm font-medium">{t("deployment.update")}</summary>
        <p className="my-3 text-xs text-muted">{t("deployment.updateHint")}</p>
        <div className="space-y-3">
          {Object.entries(data.images).map(([name, version]) => <label key={name} className="block text-xs text-muted">
            {name === "crawl4ai" ? "Crawl4AI" : "SearXNG"}
            <Input className="mt-1 w-full font-mono text-xs" value={images[name] || ""} placeholder={version} disabled={busy}
              onChange={(event) => setImages({ ...images, [name]: event.target.value })} />
          </label>)}
          <Button size="sm" disabled={busy || !data.available || !Object.values(images).some((value) => value.trim())}
            onClick={() => action.mutate({ name: "update", versions: images })}><Download size={14} />{t("deployment.applyUpdate")}</Button>
        </div>
      </details>
      <Button size="sm" variant="ghost" onClick={() => setShowConfig(!showConfig)}><Settings2 size={14} />{t("deployment.settings")}</Button>
      {showConfig && <Suspense fallback={<p className="text-sm text-muted">{t("provider.loadingConfig")}</p>}><RegisteredConfigPanel group="retrieval/deployment" /></Suspense>}
    </div>}
  </AsyncState>;
}
