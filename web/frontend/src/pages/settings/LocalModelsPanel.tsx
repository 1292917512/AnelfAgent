import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { localModelsApi } from "@/lib/api";
import type { LocalModelAsset } from "@/lib/types";
import { Badge, Button, LoadingBlock } from "@/components/ui";
import { Card } from "@/components/common/Card";
import { formatSize } from "@/lib/utils";

function statusBadge(model: LocalModelAsset, t: (k: string) => string) {
  if (model.status === "ready") return <Badge variant="ok">{t("localModels.statusReady")}</Badge>;
  if (model.status === "downloading") return <Badge variant="info">{t("localModels.statusDownloading")}</Badge>;
  if (model.status === "verifying") return <Badge variant="info">{t("localModels.statusVerifying")}</Badge>;
  if (model.status === "error") return <Badge variant="danger">{t("localModels.statusError")}</Badge>;
  return <Badge variant="neutral">{t("localModels.statusMissing")}</Badge>;
}

function ModelRow({ model, busy }: { model: LocalModelAsset; busy: boolean }) {
  const { t } = useTranslation("settings");
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["localModels"] });

  const download = useMutation({
    mutationFn: () => localModelsApi.download(model.id).then((r) => r.data),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => localModelsApi.remove(model.id).then((r) => r.data),
    onSuccess: invalidate,
  });

  const downloading = model.status === "downloading" || model.status === "verifying";
  const connecting = model.status === "downloading" && model.phase === "connecting";
  const progress =
    model.status === "downloading" && model.total
      ? Math.min(100, Math.round(((model.received ?? 0) / model.total) * 100))
      : null;

  return (
    <div className="py-3 px-3 rounded-sm border border-border/60 space-y-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-foreground">{model.name}</span>
          <span className="text-xs text-muted font-mono">{model.version}</span>
          {statusBadge(model, t)}
          {!model.runtime_ready && model.status === "ready" && (
            <Badge variant="warn">{t("localModels.runtimeMissing", { pkg: model.pip_requires })}</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!downloading && model.status !== "ready" && (
            <Button size="sm" disabled={busy || download.isPending} onClick={() => download.mutate()}>
              {model.size_bytes > 0 ? t("localModels.redownload") : t("localModels.download")}
            </Button>
          )}
          {model.status === "ready" && !downloading && (
            <Button size="sm" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t("localModels.delete")}
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs text-muted">{model.description}</p>
      {downloading && (
        <div className="space-y-1">
          <div className="h-1.5 rounded-full bg-border overflow-hidden">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: progress === null ? "100%" : `${progress}%`, opacity: progress === null ? 0.4 : 1 }}
            />
          </div>
          <div className="text-xs text-muted font-mono">
            {model.status === "verifying"
              ? t("localModels.phaseVerifying")
              : connecting
                ? t("localModels.phaseConnecting")
                : progress === null
                  ? (model.received ?? 0) > 0 ? formatSize(model.received ?? 0) : "-"
                  : `${model.received ? formatSize(model.received) : "-"} / ${model.total ? formatSize(model.total) : "-"} (${progress}%)`}
          </div>
        </div>
      )}
      {model.status === "error" && model.error && (
        <p className="text-xs text-danger break-all font-mono">{model.error}</p>
      )}
      <div className="flex items-center gap-4 text-xs text-muted">
        <span className="font-mono">{model.filename}</span>
        <span>{model.license}</span>
        <span>{model.size_bytes > 0 ? formatSize(model.size_bytes) : "-"}</span>
      </div>
    </div>
  );
}

/** 本地模型面板：语音链路等本地推理模型的下载/删除与运行时依赖安装 */
export function LocalModelsPanel() {
  const { t } = useTranslation("settings");
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["localModels"],
    queryFn: () => localModelsApi.list().then((r) => r.data),
    refetchInterval: (query) =>
      query.state.data?.models.some(
        (m) => m.status === "downloading" || m.status === "verifying") ? 1000 : false,
  });

  const installRuntime = useMutation({
    mutationFn: () => localModelsApi.installRuntime().then((r) => r.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["localModels"] }),
  });

  const runtime = data?.runtime;
  const anyDownloading =
    data?.models.some((m) => m.status === "downloading" || m.status === "verifying") ?? false;

  return (
    <div className="space-y-4">
      <Card title={t("localModels.runtimeTitle")} subtitle={t("localModels.runtimeSubtitle")}>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-sm text-foreground font-mono">onnxruntime</span>
            {runtime?.installed ? (
              <Badge variant="ok">{t("localModels.runtimeInstalled", { version: runtime.version })}</Badge>
            ) : (
              <Badge variant="warn">{t("localModels.runtimeNotInstalled")}</Badge>
            )}
          </div>
          {!runtime?.installed && (
            <Button size="sm" disabled={installRuntime.isPending} onClick={() => installRuntime.mutate()}>
              {installRuntime.isPending ? t("localModels.installing") : t("localModels.installRuntime")}
            </Button>
          )}
        </div>
        {installRuntime.data && (
          <p className={`text-xs mt-2 font-mono break-all ${installRuntime.data.ok ? "text-muted" : "text-danger"}`}>
            {String(installRuntime.data.ok ? installRuntime.data.stdout : installRuntime.data.stderr).slice(-600)}
          </p>
        )}
      </Card>

      <Card
        title={t("localModels.title")}
        subtitle={data?.dir ? `${t("localModels.dir")}: ${data.dir}` : undefined}
        actions={
          <a href="/webui/config?key=proxy_enabled" className="text-xs text-accent hover:underline">
            {t("localModels.proxyHint")}
          </a>
        }
      >
        {isLoading ? (
          <LoadingBlock />
        ) : (
          <div className="space-y-2">
            {(data?.models ?? []).map((m) => (
              <ModelRow key={m.id} model={m} busy={anyDownloading || installRuntime.isPending} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
