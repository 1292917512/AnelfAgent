import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  ArrowLeftRight, CheckCircle2, Cloud, Database, FolderSync, Pause, Play,
  RefreshCw, XCircle,
} from "lucide-react";
import { voiceprintApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { Badge, Button, Spinner, toast } from "@/components/ui";
import { formatNs } from "./format";

/** 总览：配置体检 + 镜像对照（NAS 音源侧 ↔ 音源库侧）+ 待同步清单。
 *  全部数据自动轮询刷新（AI 改配置/后台同步后界面数秒内跟进）。 */
export function OverviewPanel() {
  const { t } = useTranslation("voiceprint");
  const queryClient = useQueryClient();

  const { data: stats } = useQuery({
    queryKey: ["voiceprintStats"],
    queryFn: () => voiceprintApi.stats().then((r) => r.data),
    refetchInterval: 10_000,
  });

  const { data: preview, isLoading: previewLoading } = useQuery({
    queryKey: ["voiceprintSyncPreview"],
    queryFn: () => voiceprintApi.syncPreview().then((r) => r.data),
    refetchInterval: 15_000,
  });

  const { data: openlist } = useQuery({
    queryKey: ["voiceprintOpenlistStatus"],
    queryFn: () => voiceprintApi.openlistStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });

  const syncMutation = useMutation({
    mutationFn: () => voiceprintApi.syncNow(),
    onSuccess: (r) => {
      const d = r.data;
      if (d.error) toast.error(d.error);
      else toast.success(t("messages.syncDone", { new: d.new, ingested: d.ingested }));
      queryClient.invalidateQueries({ queryKey: ["voiceprintStats"] });
      queryClient.invalidateQueries({ queryKey: ["voiceprintSyncPreview"] });
      queryClient.invalidateQueries({ queryKey: ["voiceprintRecordings"] });
    },
    onError: () => toast.error(t("messages.opFailed")),
  });

  const pauseMutation = useMutation({
    mutationFn: (paused: boolean) =>
      voiceprintApi.updateConfig({ voiceprint_watch_paused: paused }),
    onSuccess: (_r, paused) => {
      toast.success(paused ? t("messages.syncPaused") : t("messages.syncResumed"));
      queryClient.invalidateQueries({ queryKey: ["voiceprintStats"] });
      queryClient.invalidateQueries({ queryKey: ["voiceprintConfig"] });
    },
    onError: () => toast.error(t("messages.opFailed")),
  });

  const watch = stats?.watch;
  const lastResult = (watch?.last_result ?? {}) as Record<string, number>;
  const synced = preview?.synced ?? {};

  const checks = [
    {
      ok: stats?.funasr_configured ?? false,
      label: t("overview.checkFunasr"),
      hint: t("overview.checkFunasrHint"),
    },
    {
      ok: (watch?.enabled && !!watch?.source) ?? false,
      label: t("overview.checkWatch"),
      hint: watch?.enabled ? t("overview.checkWatchNoSource") : t("overview.checkWatchHint"),
    },
    {
      ok: stats?.ingest_enabled ?? false,
      label: t("overview.checkIngest"),
      hint: t("overview.checkIngestHint"),
      optional: true,
    },
    {
      ok: (stats?.vec_available && stats?.fts_available) ?? false,
      label: t("overview.checkIndex"),
      hint: `vec ${stats?.vec_available ? "✓" : "✗"} · fts ${stats?.fts_available ? "✓" : "✗"} · ${stats?.text_embedding_model ?? ""}`,
    },
  ];

  const reasonVariant = (reason: string) =>
    reason === "new" ? "accent" : reason === "changed" ? "warn" : "danger";

  return (
    <div className="space-y-4">
      {/* 配置体检（紧凑芯片行） */}
      <div className="flex flex-wrap items-center gap-2">
        {checks.map((c) => (
          <div
            key={c.label}
            title={c.ok ? undefined : c.hint}
            className="flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs"
          >
            {c.ok ? (
              <CheckCircle2 size={13} className="text-ok" />
            ) : (
              <XCircle size={13} className="text-warn" />
            )}
            <span className="text-foreground">{c.label}</span>
            {c.optional && <Badge variant="neutral">{t("overview.optional")}</Badge>}
            {!c.ok && <span className="text-muted hidden md:inline">· {c.hint}</span>}
          </div>
        ))}
        {openlist?.configured && (
          <div className="flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs">
            {openlist.reachable ? (
              <CheckCircle2 size={13} className="text-ok" />
            ) : (
              <XCircle size={13} className="text-danger" />
            )}
            <span className="text-foreground">OpenList</span>
            <span className={openlist.reachable ? "text-muted" : "text-danger"}>
              {openlist.reachable ? `${openlist.latency_ms}ms` : openlist.error}
            </span>
          </div>
        )}
      </div>

      {/* 镜像对照：NAS 音源侧 ↔ 音源库侧 */}
      <Card title={t("overview.mirrorTitle")}>
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr] gap-4 items-stretch">
          {/* 左：NAS 音源侧（状态分布一目了然） */}
          <div className="rounded-lg border border-border bg-elevated p-3 space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Cloud size={15} />
              {t("overview.nasSide")}
            </div>
            <div className="text-xs text-muted truncate" title={watch?.source || undefined}>
              {watch?.source || t("sync.noSource")}
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge variant={watch?.enabled ? "ok" : "neutral"}>
                {watch?.enabled ? t("sync.enabled") : t("sync.disabled")}
              </Badge>
              {watch?.paused && (
                <Badge variant="warn">{t("sync.paused")}</Badge>
              )}
              {openlist?.configured && (
                <Badge variant={openlist.reachable ? "ok" : "danger"}>
                  OpenList {openlist.reachable ? `${openlist.latency_ms}ms` : "✗"}
                </Badge>
              )}
              {preview && !preview.error && (
                <Badge variant="accent">{t("overview.nasTotal")}: {preview.nas_total}</Badge>
              )}
            </div>
            {/* 同步状态分布：待同步 / 已同步 / 无声 / 失败 / 已排除 */}
            {preview && !preview.error && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                <Badge variant={preview.pending.length ? "warn" : "ok"}>
                  {t("overview.pendingTitle")} {preview.pending.length}
                </Badge>
                <Badge variant="ok">{t("sync.status.done")} {synced.done ?? 0}</Badge>
                <Badge variant="neutral">
                  {t("sync.status.no_speech")} {synced.no_speech ?? 0}
                </Badge>
                <Badge variant={(synced.error ?? 0) > 0 ? "danger" : "neutral"}>
                  {t("sync.status.error")} {synced.error ?? 0}
                </Badge>
                {(preview.excluded ?? 0) > 0 && (
                  <Badge variant="info">
                    {t("overview.excluded")} {preview.excluded}
                  </Badge>
                )}
              </div>
            )}
            {openlist?.configured && !openlist.reachable && (
              <p className="text-xs text-danger">{openlist.error}</p>
            )}
            {preview?.error && <p className="text-xs text-warn">{preview.error}</p>}
          </div>

          {/* 中：同步控制 */}
          <div className="flex lg:flex-col items-center justify-center gap-2 px-1">
            <ArrowLeftRight size={18} className="text-muted" />
            {watch?.syncing && watch.progress ? (
              <div className="flex flex-col items-center gap-1">
                <Badge variant="warn">
                  {t("overview.syncingProgress", {
                    done: watch.progress.done,
                    total: watch.progress.total,
                  })}
                </Badge>
                {watch.progress.stage && (
                  <Badge variant="accent">
                    {t(`overview.stage.${watch.progress.stage}`)}
                    {watch.progress.batches && watch.progress.batches > 1
                      ? ` ${watch.progress.batch}/${watch.progress.batches}`
                      : ""}
                  </Badge>
                )}
                <span className="text-[10px] text-muted">
                  {formatNs(watch.progress.current_started_ns)}
                </span>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-1">
                  <Button
                    size="sm"
                    loading={syncMutation.isPending}
                    disabled={!stats?.funasr_configured || watch?.paused}
                    onClick={() => syncMutation.mutate()}
                  >
                    <FolderSync size={14} className="mr-1" />
                    {t("sync.runNow")}
                  </Button>
                  <Button
                    size="sm"
                    variant={watch?.paused ? "primary" : "secondary"}
                    loading={pauseMutation.isPending}
                    title={watch?.paused ? t("sync.resume") : t("sync.pause")}
                    onClick={() => pauseMutation.mutate(!watch?.paused)}
                  >
                    {watch?.paused ? <Play size={14} /> : <Pause size={14} />}
                  </Button>
                </div>
                <span className="text-[10px] text-muted text-center">
                  {watch?.paused ? t("sync.paused") : t("sync.lastScan")}
                  <br />
                  {formatNs(watch?.last_scan_ns ?? 0)}
                </span>
              </>
            )}
          </div>

          {/* 右：音源库侧 */}
          <div className="rounded-lg border border-border bg-elevated p-3 space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Database size={15} />
              {t("overview.libSide")}
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="ok">{t("sync.status.done")} {synced.done ?? 0}</Badge>
              <Badge variant="neutral">{t("sync.status.no_speech")} {synced.no_speech ?? 0}</Badge>
              <Badge variant={(synced.error ?? 0) > 0 ? "danger" : "neutral"}>
                {t("sync.status.error")} {synced.error ?? 0}
              </Badge>
            </div>
            <div className="grid grid-cols-3 gap-2 pt-1">
              <StatCard label={t("overview.recordings")} value={stats?.recordings ?? "-"} />
              <StatCard label={t("stats.segments")} value={stats?.segments ?? "-"} />
              <StatCard label={t("stats.speakers")} value={stats?.speakers ?? "-"} />
            </div>
          </div>
        </div>

        {/* 上一轮结果 + 错误 */}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
          {Object.keys(lastResult).length > 0 && (
            <span className="flex gap-1 flex-wrap">
              {(["new", "ingested", "deleted", "no_speech", "failed"] as const).map((k) =>
                lastResult[k] ? (
                  <Badge key={k} variant={k === "failed" ? "danger" : "neutral"}>
                    {t(`overview.result.${k}`)} {lastResult[k]}
                  </Badge>
                ) : null,
              )}
            </span>
          )}
          {watch?.last_error && <span className="text-danger">{watch.last_error}</span>}
        </div>
      </Card>

      {/* 待同步清单 */}
      <Card
        title={t("overview.pendingTitle")}
        actions={
          <div className="flex items-center gap-2">
            {preview && !preview.error && (
              <Badge variant={preview.pending.length ? "warn" : "ok"}>
                {preview.pending.length}
              </Badge>
            )}
            {preview && !preview.error && (preview.excluded ?? 0) > 0 && (
              <Badge variant="neutral">
                {t("overview.excluded")} {preview.excluded}
              </Badge>
            )}
            <Button
              size="sm" variant="ghost"
              onClick={() =>
                queryClient.invalidateQueries({ queryKey: ["voiceprintSyncPreview"] })}
            >
              <RefreshCw size={13} />
            </Button>
          </div>
        }
      >
        {previewLoading ? (
          <div className="flex justify-center py-6"><Spinner /></div>
        ) : preview?.busy ? (
          <p className="text-xs text-muted py-2">
            {watch?.progress
              ? t("overview.syncingProgress", {
                  done: watch.progress.done,
                  total: watch.progress.total,
                })
              : t("overview.syncBusy")}
          </p>
        ) : preview?.error ? (
          <p className="text-xs text-warn py-2">{preview.error}</p>
        ) : (preview?.pending.length ?? 0) === 0 ? (
          <p className="text-xs text-muted py-2">{t("overview.allSynced")}</p>
        ) : (
          <div className="space-y-1">
            {preview!.pending.map((u) => (
              <div
                key={u.path}
                className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5 text-xs"
              >
                <Badge variant={reasonVariant(u.reason)}>
                  {t(`overview.reason.${u.reason}`)}
                </Badge>
                <span className="text-foreground whitespace-nowrap">
                  {formatNs(u.started_ns)}
                </span>
                <span className="text-muted whitespace-nowrap">
                  {u.file_count} {t("recordings.files")}
                </span>
                <div className="flex-1" />
                <span className="text-muted truncate max-w-56" title={u.path}>{u.path}</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* 库规模 */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatCard label={t("stats.speakers")} value={stats?.speakers ?? "-"} />
        <StatCard label={t("stats.pending")} value={stats?.pending_speakers ?? "-"}
          variant={stats?.pending_speakers ? "warn" : "default"} />
        <StatCard label={t("overview.recordings")} value={stats?.recordings ?? "-"} />
        <StatCard label={t("stats.segments")} value={stats?.segments ?? "-"} />
        <StatCard label={t("stats.unread")} value={stats?.unread_segments ?? "-"}
          variant={stats?.unread_segments ? "warn" : "default"} />
        <StatCard label={t("overview.samples")} value={stats?.samples ?? "-"} />
      </div>
    </div>
  );
}
