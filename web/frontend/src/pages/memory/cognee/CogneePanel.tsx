import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { RefreshCw, Database, Sparkles, Upload } from "lucide-react";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";
import { type FieldMeta } from "@/pages/config/AppField";
import { ModelConfigCard } from "@/pages/memory/cognee/ModelConfigCard";
import { memoryApi } from "@/lib/api";
import type { CogneeResolvedInfo } from "@/lib/types";

function ResolvedLine({ label, info }: { label: string; info?: CogneeResolvedInfo }) {
  const { t } = useTranslation("memory");
  if (!info || !info.model) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="text-muted w-20 flex-shrink-0">{label}</span>
        <span className="text-muted">{t("cognee.notResolved")}</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted w-20 flex-shrink-0">{label}</span>
      <span className="font-mono text-heading">
        {info.provider}/{info.model}
      </span>
      {info.instructor_mode && (
        <span className="px-1.5 py-0.5 rounded bg-elevated border border-border text-muted">
          {info.instructor_mode}
        </span>
      )}
      {info.endpoint && (
        <span className="text-muted truncate max-w-64">{info.endpoint}</span>
      )}
    </div>
  );
}

export function CogneePanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();

  const { data: status } = useQuery({
    queryKey: ["cogneeStatus"],
    queryFn: () => memoryApi.cognee.status().then((r) => r.data),
    refetchInterval: 10000,
  });
  const { data: datasets } = useQuery({
    queryKey: ["cogneeDatasets"],
    queryFn: () => memoryApi.cognee.datasets().then((r) => r.data),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["cogneeStatus"] });
    queryClient.invalidateQueries({ queryKey: ["memoryHealth"] });
  };
  const retryMutation = useMutation({ mutationFn: () => memoryApi.cognee.retry(), onSuccess: invalidate });
  const backfillMutation = useMutation({ mutationFn: () => memoryApi.cognee.backfill(0, false), onSuccess: invalidate });
  const improveMutation = useMutation({
    mutationFn: (name: string) => memoryApi.cognee.improve(name),
    onSuccess: invalidate,
  });

  const availability = status?.availability;
  const sync = status?.sync;

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
    { key: "native_weight", label: t("cogneeFields.native_weight"), type: "float", desc: t("cogneeDescs.native_weight") },
    { key: "cognee_weight", label: t("cogneeFields.cognee_weight"), type: "float", desc: t("cogneeDescs.cognee_weight") },
    { key: "rrf_k", label: t("cogneeFields.rrf_k"), type: "int", desc: t("cogneeDescs.rrf_k") },
    { key: "recall_pool_multiplier", label: t("cogneeFields.recall_pool_multiplier"), type: "int", desc: t("cogneeDescs.recall_pool_multiplier") },
  ];

  return (
    <div className="space-y-4">
      <Card
        title={t("cognee.statusTitle")}
        subtitle={availability?.reason || t("cognee.statusSubtitle")}
        actions={
          <div className="flex gap-2">
            {(sync?.failed || 0) > 0 && (
              <button
                onClick={() => retryMutation.mutate()}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
              >
                <RefreshCw size={14} /> {t("retryFailedSync")}
              </button>
            )}
            <button
              onClick={() => { if (window.confirm(t("cognee.backfillConfirm"))) backfillMutation.mutate(); }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <Upload size={14} /> {t("cognee.backfill")}
            </button>
          </div>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          <StatCard
            label={t("cogneeInstalled")}
            value={availability?.installed ? (availability.version || t("common:available")) : t("common:unavailable")}
            variant={availability?.installed ? "ok" : "default"}
          />
          <StatCard
            label={t("cogneeReady")}
            value={availability?.ready ? t("common:available") : t("common:unavailable")}
            variant={availability?.ready ? "ok" : "default"}
          />
          <StatCard
            label={t("cognee.syncWorker")}
            value={sync?.running ? t("cognee.running") : t("cognee.stopped")}
            variant={sync?.running ? "ok" : "default"}
          />
          <StatCard label={t("cognee.synced")} value={String(sync?.synced || 0)} />
          <StatCard label={t("syncPending")} value={String(sync?.pending || 0)} />
          <StatCard
            label={t("syncFailed")}
            value={String(sync?.failed || 0)}
            variant={(sync?.failed || 0) > 0 ? "danger" : "default"}
          />
        </div>
        <div className="space-y-1.5 pt-2 border-t border-border">
          <ResolvedLine label={t("cognee.chatModel")} info={status?.resolved?.chat} />
          <ResolvedLine label={t("cognee.embeddingModel")} info={status?.resolved?.embedding} />
        </div>
        {sync?.last_error && (
          <div className="mt-3 p-3 rounded-md bg-danger-subtle border border-danger text-xs text-danger">
            {t("cognee.lastError")}: {sync.last_error}
          </div>
        )}
      </Card>

      <ModelConfigCard kind="chat" />
      <ModelConfigCard kind="embedding" />

      <Card title={t("cognee.datasetsTitle")} subtitle={t("cognee.datasetsSubtitle")}>
        {datasets && datasets.length > 0 ? (
          <div className="space-y-2">
            {datasets.map((ds) => (
              <div
                key={ds.id}
                className="flex items-center justify-between px-3 py-2 rounded-md bg-elevated border border-border"
              >
                <div className="flex items-center gap-2 text-sm">
                  <Database size={14} className="text-muted" />
                  <span className="text-heading">{ds.name}</span>
                  <span className="text-xs text-muted font-mono">{ds.id}</span>
                </div>
                <button
                  onClick={() => improveMutation.mutate(ds.name)}
                  disabled={improveMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
                >
                  <Sparkles size={14} /> {t("cognee.improve")}
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">{t("cognee.noDatasets")}</p>
        )}
      </Card>

      <ConfigFormPanel
        title={t("cognee.generalTitle")}
        subtitle={t("cognee.generalSubtitle")}
        fields={generalFields}
        queryKey="cogneeConfig"
        fetchFn={() => memoryApi.cognee.getConfig().then((r) => r.data as unknown as Record<string, unknown>)}
        saveFn={(values) => memoryApi.cognee.saveConfig(values as Parameters<typeof memoryApi.cognee.saveConfig>[0])}
        extraInvalidateKeys={["cogneeStatus", "memoryHealth"]}
        note={t("cognee.hotApplyNote")}
      />
    </div>
  );
}
