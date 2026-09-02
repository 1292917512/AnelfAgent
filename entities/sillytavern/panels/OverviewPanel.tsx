import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Play, RotateCw, Square } from "lucide-react";
import { apiErrorMessage, sillytavernApi } from "./api";
import { Badge, Button, ConfirmDialog, LoadingBlock, toast } from "@/components/ui";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { StatusDot } from "@/components/common/StatusDot";

function fmtUptime(sec: number | null, u: (d: number, h: number, m: number, s: number) => string): string {
  if (!sec || sec <= 0) return "--";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return u(d, h, m, s);
}

/** 运行状态面板：状态大卡 + 启停控制 + 指标 + 日志尾部 */
export function OverviewPanel() {
  const { t } = useTranslation(["sillytavern", "common"]);
  const queryClient = useQueryClient();
  const logRef = useRef<HTMLPreElement>(null);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);

  const { data: status, isLoading } = useQuery({
    queryKey: ["st", "status"],
    queryFn: () => sillytavernApi.status().then((r) => r.data),
    refetchInterval: 10_000,
  });

  const { data: logs } = useQuery({
    queryKey: ["st", "logs"],
    queryFn: () => sillytavernApi.logs().then((r) => r.data),
    refetchInterval: 5000,
  });

  const invalidateAll = () => queryClient.invalidateQueries({ queryKey: ["st"] });

  const startMut = useMutation({
    mutationFn: () => sillytavernApi.start(),
    onSuccess: () => {
      toast.success(t("sillytavern:overview.startSuccess"));
      invalidateAll();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });
  const stopMut = useMutation({
    mutationFn: () => sillytavernApi.stop(),
    onSuccess: () => {
      toast.success(t("sillytavern:overview.stopSuccess"));
      invalidateAll();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });
  const restartMut = useMutation({
    mutationFn: () => sillytavernApi.restart(),
    onSuccess: () => {
      toast.success(t("sillytavern:overview.restartSuccess"));
      invalidateAll();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  const busy = startMut.isPending || stopMut.isPending || restartMut.isPending;

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [logs?.log_tail]);

  if (isLoading || !status) return <LoadingBlock label={t("common:loading")} />;

  const running = status.running;

  return (
    <div className="space-y-4">
      {/* 状态大卡片 */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <StatusDot status={running ? "ok" : "offline"} />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-lg font-semibold text-heading">
                  {running
                    ? t("sillytavern:overview.running")
                    : t("sillytavern:overview.stopped")}
                </span>
                <Badge variant={status.managed ? "info" : "warn"}>
                  {status.managed
                    ? t("sillytavern:overview.managed")
                    : t("sillytavern:overview.unmanaged")}
                </Badge>
                {status.auto_start && (
                  <Badge variant="accent">{t("sillytavern:overview.autoStart")}</Badge>
                )}
              </div>
              {status.url && (
                <p className="text-xs text-muted mt-1 truncate">
                  {t("sillytavern:overview.url")}: {status.url}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {running && status.url && (
              <a href={status.url} target="_blank" rel="noreferrer">
                <Button variant="secondary" size="sm">
                  <ExternalLink size={14} />
                  {t("sillytavern:overview.openWeb")}
                </Button>
              </a>
            )}
            {running ? (
              <Button
                variant="primary"
                size="sm"
                loading={restartMut.isPending}
                disabled={busy}
                onClick={() => restartMut.mutate()}
              >
                <RotateCw size={14} />
                {restartMut.isPending
                  ? t("sillytavern:overview.restarting")
                  : t("sillytavern:overview.restart")}
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                loading={startMut.isPending}
                disabled={busy}
                onClick={() => startMut.mutate()}
              >
                <Play size={14} />
                {startMut.isPending
                  ? t("sillytavern:overview.starting")
                  : t("sillytavern:overview.start")}
              </Button>
            )}
            {running && (
              <Button
                variant="danger"
                size="sm"
                loading={stopMut.isPending}
                disabled={busy && !stopMut.isPending}
                onClick={() => setStopConfirmOpen(true)}
              >
                <Square size={14} />
                {stopMut.isPending
                  ? t("sillytavern:overview.stopping")
                  : t("sillytavern:overview.stop")}
              </Button>
            )}
          </div>
        </div>
      </Card>

      {/* 指标卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label={t("sillytavern:overview.version")} value={status.version || "--"} />
        <StatCard
          label={t("sillytavern:overview.pid")}
          value={running && status.pid ? status.pid : "--"}
        />
        <StatCard
          label={t("sillytavern:overview.uptime")}
          value={
            running
              ? fmtUptime(status.uptime_sec, (d, h, m, s) => {
                  const parts: string[] = [];
                  if (d) parts.push(`${d}${t("sillytavern:overview.day")}`);
                  if (h) parts.push(`${h}${t("sillytavern:overview.hour")}`);
                  if (m) parts.push(`${m}${t("sillytavern:overview.minute")}`);
                  parts.push(`${s}${t("sillytavern:overview.second")}`);
                  return parts.join(" ");
                })
              : "--"
          }
          variant={running ? "ok" : "default"}
        />
        <StatCard label={t("sillytavern:overview.port")} value={status.port || "--"} />
      </div>

      {/* 日志尾部 */}
      <Card title={t("sillytavern:overview.logTail")}>
        <pre
          ref={logRef}
          className="max-h-72 overflow-auto rounded-md bg-elevated border border-border p-3 text-xs font-mono text-foreground whitespace-pre-wrap break-all"
        >
          {logs?.log_tail?.trim() || t("sillytavern:overview.logEmpty")}
        </pre>
      </Card>

      <ConfirmDialog
        open={stopConfirmOpen}
        onClose={() => setStopConfirmOpen(false)}
        onConfirm={() => {
          stopMut.mutate();
          setStopConfirmOpen(false);
        }}
        title={t("sillytavern:overview.confirmStopTitle")}
        message={t("sillytavern:overview.confirmStopMessage")}
        confirmText={t("sillytavern:overview.stop")}
        cancelText={t("sillytavern:common.cancel")}
        danger
        loading={stopMut.isPending}
      />
    </div>
  );
}
