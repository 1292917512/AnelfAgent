import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Play, RefreshCw, Square } from "lucide-react";
import { apiErrorMessage } from "@/lib/api";
import { nonebotApi } from "../api";
import { StatusDot } from "@/components/common/StatusDot";
import { Badge, Button, EmptyState, LoadingBlock, toast } from "@/components/ui";

function statusDotOf(status: string): "ok" | "warn" | "danger" | "offline" {
  if (status === "running") return "ok";
  if (status === "starting" || status === "reconnecting") return "warn";
  if (status === "error") return "danger";
  return "offline";
}

/** NoneBot 客户端总览：worker 进程 / 桥接连接 / 在线 Bot / 生命周期控制 */
export function OverviewPanel() {
  const { t } = useTranslation("nonebot");
  const queryClient = useQueryClient();

  const { data: status, isLoading, isError, refetch } = useQuery({
    queryKey: ["nonebotStatus"],
    queryFn: () => nonebotApi.status().then((r) => r.data),
    refetchInterval: 5000,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
    queryClient.invalidateQueries({ queryKey: ["nonebotEnv"] });
  };

  const bootstrapMutation = useMutation({
    mutationFn: () => nonebotApi.envBootstrap(),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(r.data.message || t("runtime.bootstrapped"));
      else toast.error(r.data?.error || t("toast.requestFailed"));
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  const restartMutation = useMutation({
    mutationFn: () => nonebotApi.restart(),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(t("toast.restartTriggered"));
      else toast.error(r.data?.error || t("toast.restartFailed"));
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  const lifecycleMutation = useMutation({
    mutationFn: (action: "start" | "stop") =>
      action === "start" ? nonebotApi.workerStart() : nonebotApi.workerStop(),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(r.data.message || t("toast.done"));
      else toast.error(r.data?.error || t("toast.requestFailed"));
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;
  if (isError || !status?.ready) {
    return (
      <EmptyState
        icon={Bot}
        title={t("loadFailed")}
        action={
          <Button variant="secondary" size="sm" onClick={() => refetch()}>
            <RefreshCw size={14} />
            {t("retry")}
          </Button>
        }
      />
    );
  }

  const channel = status.channel_status;
  const worker = channel?.worker;
  const snapshot = channel?.worker_snapshot;
  const dot = status.enabled
    ? statusDotOf(channel?.status || "stopped")
    : "offline";
  const workerAlive = !!worker?.alive;

  return (
    <div className="space-y-3">
      {/* 未启用引导卡 */}
      {!status.enabled && (
        <div className="rounded-lg border border-accent/30 bg-accent-subtle p-4">
          <h3 className="text-sm font-medium text-accent">{t("guide.title")}</h3>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-muted">
            <li>{t("guide.step1")}</li>
            <li>{t("guide.step2")}</li>
            <li>{t("guide.step3")}</li>
            <li>{t("guide.step4")}</li>
          </ol>
          <div className="mt-3 flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              disabled={bootstrapMutation.isPending || status.env?.venv_ready}
              onClick={() => bootstrapMutation.mutate()}
            >
              {bootstrapMutation.isPending && <RefreshCw size={14} className="animate-spin" />}
              {status.env?.venv_ready ? t("runtime.ready") : t("runtime.bootstrap")}
            </Button>
            {status.env?.venv_ready && (
              <Badge variant="ok">{t("runtime.ready")}</Badge>
            )}
            {!status.env?.uv_found && (
              <span className="text-xs text-warn">{t("runtime.uvMissingHint")}</span>
            )}
          </div>
        </div>
      )}

      {/* 状态总览卡 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <StatusDot status={dot} />
            <span className="text-sm font-medium">
              {status.enabled ? channel?.name : t("overview.disabled")}
            </span>
            {status.enabled && (
              <Badge variant="neutral">{t(`overview.status.${channel?.status || "stopped"}`)}</Badge>
            )}
            {snapshot?.nonebot_version && (
              <Badge variant="accent">NoneBot {snapshot.nonebot_version}</Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            {status.enabled && workerAlive && (
              <Button
                variant="secondary"
                size="sm"
                disabled={lifecycleMutation.isPending}
                onClick={() => lifecycleMutation.mutate("stop")}
                title={t("overview.stopWorker")}
              >
                <Square size={14} />
                {t("overview.stop")}
              </Button>
            )}
            {status.enabled && !workerAlive && (
              <Button
                variant="secondary"
                size="sm"
                disabled={lifecycleMutation.isPending}
                onClick={() => lifecycleMutation.mutate("start")}
                title={t("overview.startWorker")}
              >
                <Play size={14} />
                {t("overview.start")}
              </Button>
            )}
            <Button
              variant="secondary"
              size="sm"
              disabled={!status.registered || restartMutation.isPending}
              onClick={() => restartMutation.mutate()}
            >
              <RefreshCw size={14} className={restartMutation.isPending ? "animate-spin" : ""} />
              {t("overview.restart")}
            </Button>
          </div>
        </div>

        {status.enabled && worker && (
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              { label: t("overview.processAlive"), value: worker.alive ? t("overview.on") : t("overview.off") },
              { label: t("overview.venvReady"), value: worker.venv_ready ? t("overview.on") : t("overview.off") },
              { label: t("overview.bridgeConnected"), value: channel?.bridge_connected ? t("overview.on") : t("overview.off") },
              { label: t("overview.onlineBots"), value: String(snapshot?.bots?.length || 0) },
            ].map((item) => (
              <div key={item.label} className="rounded-md border border-border bg-elevated px-3 py-2">
                <div className="text-[11px] text-muted">{item.label}</div>
                <div className="text-sm font-medium">{item.value}</div>
              </div>
            ))}
          </div>
        )}

        {status.enabled && (
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            {worker?.pid != null && worker.alive && <span>PID {worker.pid}</span>}
            {channel?.worker_base_url && (
              <span>
                {t("overview.workerBaseUrl")}: <code className="text-foreground">{channel.worker_base_url}</code>
              </span>
            )}
            {worker && worker.auto_restart && <span>{t("overview.autoRestartOn")}</span>}
            <span>{t(channel?.intercept_all ? "overview.interceptOn" : "overview.passthroughOn")}</span>
          </div>
        )}
      </div>

      {/* 安装进度 */}
      {status.install?.running && (
        <div className="rounded-lg border border-warn/30 bg-warn-subtle p-4">
          <div className="flex items-center gap-2 text-sm text-warn">
            <RefreshCw size={14} className="animate-spin" />
            {t("overview.installing", { packages: status.install.packages.join(", ") })}
          </div>
        </div>
      )}

      {/* 在线 Bot 列表 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <h3 className="mb-2 text-sm font-medium">{t("overview.bots")}</h3>
        {(snapshot?.bots?.length || 0) === 0 ? (
          <p className="text-xs text-muted">{t("overview.noBots")}</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {snapshot!.bots.map((bot) => (
              <div key={bot.bot_id} className="flex items-center gap-2 rounded-md border border-border bg-elevated px-3 py-2">
                <StatusDot status="ok" />
                <span className="text-sm font-mono">{bot.bot_id}</span>
                <Badge variant="info">{bot.adapter}</Badge>
              </div>
            ))}
          </div>
        )}
        <p className="mt-2 text-xs text-muted">{t("overview.botsHint")}</p>
      </div>

      {/* 已启用适配器 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <h3 className="mb-2 text-sm font-medium">{t("overview.adapters")}</h3>
        {(snapshot?.adapters?.length || 0) === 0 ? (
          <p className="text-xs text-muted">{t("overview.noAdapters")}</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {snapshot!.adapters.map((a) => (
              <Badge key={a} variant="accent2">{a}</Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
