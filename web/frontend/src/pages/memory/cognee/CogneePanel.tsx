import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { RefreshCw, Upload, Shrink } from "lucide-react";
import { ProbeStatusCard } from "./ProbeStatusCard";
import { DatasetMaintenanceCard } from "./DatasetMaintenanceCard";
import { ProjectionStatusCard } from "./ProjectionStatusCard";
import { StorageDetailsCard } from "./StorageDetailsCard";
import { QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { ConfirmDialog, toast } from "@/components/ui";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";
import { type FieldMeta } from "@/pages/config/AppField";
import { ModelConfigCard, ResolvedModelLine } from "@/pages/memory/cognee/ModelConfigCard";
import { RecallTester } from "@/pages/memory/RecallTester";
import { memoryApi } from "@/lib/api";
import { formatSize } from "@/lib/utils";
import type { ConfigValues } from "@/lib/types";

export function CogneePanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const [confirmation, setConfirmation] = useState<"rebuild" | "freshRebuild" | "backfill" | null>(null);

  const { data: status, error: statusError, dataUpdatedAt, refetch: refreshStatus } = useQuery({
    queryKey: ["cogneeStatus"],
    queryFn: () => memoryApi.cognee.status().then((r) => r.data),
    refetchInterval: 5000,
    throwOnError: false,
  });
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["cogneeStatus"] });
    queryClient.invalidateQueries({ queryKey: ["memoryHealth"] });
    queryClient.invalidateQueries({ queryKey: ["cogneeDatasets"] });
  };
  const retryMutation = useMutation({ mutationFn: () => memoryApi.cognee.retry(), onSuccess: invalidate });
  const backfillMutation = useMutation({ mutationFn: () => memoryApi.cognee.backfill(0, false), onSuccess: invalidate });
  const rebuildMutation = useMutation({
    mutationFn: (restart: boolean) => memoryApi.cognee.rebuild(restart),
    onSuccess: invalidate,
  });
  const compactMutation = useMutation({
    mutationFn: () => memoryApi.cognee.compact(),
    onSuccess: (resp) => {
      invalidate();
      const data = resp?.data;
      if (data?.error) {
        toast.error(data.error);
      } else if (data?.scheduled) {
        toast.success(t("cognee.compactScheduled"));
      } else {
        toast.success(
          t("cognee.compactDone", { size: formatSize(data?.result?.bytes_reclaimed ?? 0) }),
        );
      }
    },
    onError: () => toast.error(t("cognee.compactFailed")),
  });

  const availability = status?.availability;
  const sync = status?.sync;
  const storage = status?.storage;

  const generalFields: FieldMeta[] = [
    { key: "enabled", label: t("cogneeFields.enabled"), type: "bool", desc: t("cogneeDescs.enabled") },
    { key: "sync_enabled", label: t("cogneeFields.sync_enabled"), type: "bool", desc: t("cogneeDescs.sync_enabled") },
    { key: "recall_enabled", label: t("cogneeFields.recall_enabled"), type: "bool", desc: t("cogneeDescs.recall_enabled") },
    { key: "dataset_prefix", label: t("cogneeFields.dataset_prefix"), type: "string", desc: t("cogneeDescs.dataset_prefix") },
    { key: "timeout_seconds", label: t("cogneeFields.timeout_seconds"), type: "float", desc: t("cogneeDescs.timeout_seconds") },
    { key: "pipeline_timeout_seconds", label: t("cogneeFields.pipeline_timeout_seconds"), type: "float", desc: t("cogneeDescs.pipeline_timeout_seconds") },
    { key: "improve_interval_seconds", label: t("cogneeFields.improve_interval_seconds"), type: "float", desc: t("cogneeDescs.improve_interval_seconds") },
    { key: "sync_interval_seconds", label: t("cogneeFields.sync_interval_seconds"), type: "float", desc: t("cogneeDescs.sync_interval_seconds") },
    { key: "sync_batch_size", label: t("cogneeFields.sync_batch_size"), type: "int", desc: t("cogneeDescs.sync_batch_size") },
    { key: "max_retries", label: t("cogneeFields.max_retries"), type: "int", desc: t("cogneeDescs.max_retries") },
    { key: "compact_enabled", label: t("cogneeFields.compact_enabled"), type: "bool", desc: t("cogneeDescs.compact_enabled") },
    { key: "compact_interval_seconds", label: t("cogneeFields.compact_interval_seconds"), type: "float", desc: t("cogneeDescs.compact_interval_seconds") },
    { key: "compact_retention_days", label: t("cogneeFields.compact_retention_days"), type: "float", desc: t("cogneeDescs.compact_retention_days") },
    { key: "write_breaker_enabled", label: t("cogneeFields.write_breaker_enabled"), type: "bool", desc: t("cogneeDescs.write_breaker_enabled") },
    { key: "write_breaker_threshold_mb", label: t("cogneeFields.write_breaker_threshold_mb"), type: "float", desc: t("cogneeDescs.write_breaker_threshold_mb") },
    { key: "write_breaker_window_seconds", label: t("cogneeFields.write_breaker_window_seconds"), type: "float", desc: t("cogneeDescs.write_breaker_window_seconds") },
    { key: "write_breaker_cooldown_seconds", label: t("cogneeFields.write_breaker_cooldown_seconds"), type: "float", desc: t("cogneeDescs.write_breaker_cooldown_seconds") },
    { key: "native_weight", label: t("cogneeFields.native_weight"), type: "float", desc: t("cogneeDescs.native_weight") },
    { key: "cognee_weight", label: t("cogneeFields.cognee_weight"), type: "float", desc: t("cogneeDescs.cognee_weight") },
    { key: "rrf_k", label: t("cogneeFields.rrf_k"), type: "int", desc: t("cogneeDescs.rrf_k") },
    { key: "recall_pool_multiplier", label: t("cogneeFields.recall_pool_multiplier"), type: "int", desc: t("cogneeDescs.recall_pool_multiplier") },
  ];

  return (
    <div className="space-y-4">
      <ProjectionStatusCard state={status?.projection} sync={sync} updatedAt={dataUpdatedAt} stale={!!statusError}
        enabled={availability?.enabled ?? false} />
      <div className="rounded-xl border border-border bg-elevated p-3 space-y-2 text-sm">
        <p className="text-heading">{t("cognee.setupHint")}</p>
        <Link to="/models" className="text-accent hover:underline">{t("cognee.configureModels")}</Link>
        <p className="text-xs text-muted">{t("cognee.usageHint")}</p>
      </div>
      {rebuildMutation.error && <QueryError error={rebuildMutation.error} retry={() => rebuildMutation.mutate(rebuildMutation.variables ?? false)} compact />}
      <RecallTester preset="cognee" />

      <ProbeStatusCard />
      <Card
        title={t("cognee.statusTitle")}
        subtitle={availability?.reason || t("cognee.statusSubtitle")}
        actions={
          <div className="flex flex-wrap gap-2">
            {((sync?.failed || 0) > 0 || sync?.publication?.error) && (
              <button
                onClick={() => retryMutation.mutate()}
                disabled={retryMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
              >
                <RefreshCw size={14} /> {t("retryFailedSync")}
              </button>
            )}
            <button
              onClick={() => compactMutation.mutate()}
              disabled={!availability?.ready || compactMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <Shrink size={14} /> {t("cognee.compact")}
            </button>
            <button
              onClick={() => setConfirmation("backfill")}
              disabled={backfillMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <Upload size={14} /> {t("cognee.backfill")}
            </button>
            <button
              onClick={() => setConfirmation("rebuild")}
              disabled={!availability?.ready || rebuildMutation.isPending || status?.projection?.rebuild_requested || status?.retirement?.running
                || ["requested", "scanning", "building", "verifying"].includes(status?.projection?.phase ?? "")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-danger-subtle text-danger hover:bg-[rgba(239,68,68,0.15)] transition-all"
            >
              <RefreshCw size={14} /> {t("cognee.rebuild")}
            </button>
            <button
              onClick={() => setConfirmation("freshRebuild")}
              disabled={!availability?.ready || rebuildMutation.isPending || status?.projection?.rebuild_requested || status?.retirement?.running}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-danger-subtle text-danger hover:bg-[rgba(239,68,68,0.15)] transition-all"
            >
              <RefreshCw size={14} /> {t("cognee.freshRebuild")}
            </button>
          </div>
        }
      >
        {statusError && <QueryError error={statusError} retry={() => void refreshStatus()} compact />}
        {sync?.configuration_pending && (
          <p role="status" className="mb-3 rounded-lg border border-accent/20 bg-accent/5 p-3 text-sm text-accent">{t("cognee.applyPending")}</p>
        )}
        {sync?.configuration_error && (
          <p role="alert" className="mb-3 rounded-lg border border-danger/30 bg-danger-subtle p-3 text-sm text-danger">{t("cognee.applyFailed")}: {sync.configuration_error}</p>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          <StatCard
            label={t("cogneeInstalled")}
            value={availability?.installed ? (availability.version || t("common:available")) : t("common:unavailable")}
            variant={availability?.installed ? "ok" : "default"}
          />
          <StatCard
            label={t("cogneeReady")}
            value={availability?.configuration_required ? t("cognee.waitingForConfig") : availability?.ready ? t("common:available") : t("common:unavailable")}
            variant={availability?.ready ? "ok" : "default"}
          />
          <StatCard
            label={t("cognee.syncWorker")}
            value={availability?.configuration_required ? t("cognee.waitingForConfig") : sync?.running ? t("cognee.running") : t("cognee.stopped")}
            variant={sync?.running && availability?.ready ? "ok" : "default"}
          />
          <StatCard label={t("cognee.synced")} value={String(sync?.synced || 0)} />
          <StatCard label={t("syncPending")} value={String(sync?.pending || 0)} />
          <StatCard
            label={t("syncFailed")}
            value={String(sync?.failed || 0)}
            variant={(sync?.failed || 0) > 0 ? "danger" : "default"}
          />
          <StatCard
            label={t("cognee.storage")}
            value={storage ? formatSize(storage.total_bytes) : "—"}
          />
        </div>
        {storage && storage.total_bytes > 0 && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 pb-2 text-[11px] text-muted">
            <span>{t("cognee.storageLance")}: {formatSize(storage.lance_bytes)}</span>
            <span>{t("cognee.storageGraph")}: {formatSize(storage.graph_bytes)}</span>
            <span>{t("cognee.storageMetadata")}: {formatSize(storage.metadata_bytes)}</span>
            <span>{t("cognee.storageData")}: {formatSize(storage.data_bytes)}</span>
          </div>
        )}
        {sync?.last_compact_summary && (
          <div className="pb-2 text-[11px] text-muted">
            {t("cognee.lastCompact")}: {sync.last_compact_summary}
            {sync.last_compact_at
              ? `（${new Date(sync.last_compact_at * 1000).toLocaleString()}）`
              : ""}
          </div>
        )}
        <div className="space-y-1.5 pt-2 border-t border-border">
          <ResolvedModelLine label={t("cognee.chatModel")} info={status?.resolved?.chat} />
          <ResolvedModelLine label={t("cognee.embeddingModel")} info={status?.resolved?.embedding} />
        </div>
        {sync?.last_error && (
          <div className="mt-3 p-3 rounded-md bg-danger-subtle border border-danger text-xs text-danger">
            {t("cognee.lastError")}: {sync.last_error}
          </div>
        )}
      </Card>

      <ModelConfigCard kind="chat" />
      <ModelConfigCard kind="embedding" />

      <StorageDetailsCard status={status} />
      <DatasetMaintenanceCard ready={availability?.ready ?? false}
        namespace={status?.projection?.phase === "ready" ? status.projection.namespace : ""} status={status} />

      <ConfigFormPanel
        title={t("cognee.generalTitle")}
        subtitle={t("cognee.generalSubtitle")}
        fields={generalFields}
        queryKey="cogneeConfig"
        fetchFn={() => memoryApi.cognee.getConfig().then((r) => r.data as unknown as ConfigValues)}
        saveFn={(values) => memoryApi.cognee.saveConfig(values as Parameters<typeof memoryApi.cognee.saveConfig>[0])}
        extraInvalidateKeys={["cogneeStatus", "memoryHealth"]}
        note={t("cognee.hotApplyNote")}
      />
      <ConfirmDialog
        open={confirmation !== null}
        title={t(`cognee.${confirmation ?? "rebuild"}`)}
        message={t(`cognee.${confirmation ?? "rebuild"}Confirm`)}
        onClose={() => setConfirmation(null)}
        onConfirm={() => {
          if (confirmation === "backfill") backfillMutation.mutate();
          if (confirmation === "rebuild" || confirmation === "freshRebuild") rebuildMutation.mutate(confirmation === "freshRebuild");
          setConfirmation(null);
        }}
      />
    </div>
  );
}
