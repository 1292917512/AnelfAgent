import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, Upload, Shrink, Settings, Search } from "lucide-react";
import { ProbeStatusCard } from "./ProbeStatusCard";
import { DatasetMaintenanceCard } from "./DatasetMaintenanceCard";
import { ProjectionStatusCard } from "./ProjectionStatusCard";
import { StorageDetailsCard } from "./StorageDetailsCard";
import { QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { Button, Modal, ConfirmDialog, toast } from "@/components/ui";
import { ResolvedModelLine } from "./ResolvedModelLine";
import { CogneeSettings } from "./CogneeSettings";
import { RecallTester } from "@/pages/memory/RecallTester";
import { memoryApi } from "@/lib/api";
import { formatSize } from "@/lib/utils";

export function CogneePanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const [detail, setDetail] = useState<"models" | "recall" | null>(null);
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
  const retryMutation = useMutation({ mutationFn: () => memoryApi.cognee.retry("local"), onSuccess: invalidate });
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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">{t("cognee.workspaceHint")}</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => setDetail("recall")}><Search size={14} />{t("cognee.recallDialog")}</Button>
          <Button variant="secondary" size="sm" onClick={() => setDetail("models")}><Settings size={14} />{t("cognee.configDialog")}</Button>
        </div>
      </div>
      <ProjectionStatusCard state={status?.projection} sync={sync} updatedAt={dataUpdatedAt} stale={!!statusError}
        enabled={availability?.enabled ?? false} onRetry={() => retryMutation.mutate()} retrying={retryMutation.isPending} />
      {retryMutation.error && <QueryError error={retryMutation.error} compact />}
      {rebuildMutation.error && <QueryError error={rebuildMutation.error} retry={() => rebuildMutation.mutate(rebuildMutation.variables ?? false)} compact />}
      <Card
        title={t("cognee.statusTitle")}
        subtitle={availability?.reason || t("cognee.statusSubtitle")}
        actions={
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => compactMutation.mutate()}
              disabled={!availability?.ready || compactMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-[color,background-color,border-color,box-shadow,opacity]"
            >
              <Shrink size={14} /> {t("cognee.compact")}
            </button>
            <button
              onClick={() => setConfirmation("backfill")}
              disabled={backfillMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-[color,background-color,border-color,box-shadow,opacity]"
            >
              <Upload size={14} /> {t("cognee.backfill")}
            </button>
            <button
              onClick={() => setConfirmation("rebuild")}
              disabled={!availability?.ready || rebuildMutation.isPending || status?.projection?.rebuild_requested || status?.retirement?.running
                || ["requested", "scanning", "building", "verifying"].includes(status?.projection?.phase ?? "")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-danger-subtle text-danger hover:bg-[rgba(239,68,68,0.15)] transition-[color,background-color,border-color,box-shadow,opacity]"
            >
              <RefreshCw size={14} /> {t("cognee.rebuild")}
            </button>
            <button
              onClick={() => setConfirmation("freshRebuild")}
              disabled={!availability?.ready || rebuildMutation.isPending || status?.projection?.rebuild_requested || status?.retirement?.running}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-danger-subtle text-danger hover:bg-[rgba(239,68,68,0.15)] transition-[color,background-color,border-color,box-shadow,opacity]"
            >
              <RefreshCw size={14} /> {t("cognee.freshRebuild")}
            </button>
          </div>
        }
      >
        {statusError && <QueryError error={statusError} retry={() => void refreshStatus()} compact />}
        <dl className="mb-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-xs text-muted">{t("cogneeInstalled")}</dt>
            <dd className="mt-1 font-medium text-heading">{availability ? availability.installed ? (availability.version || t("common:available")) : t("common:unavailable") : "—"}</dd></div>
          <div><dt className="text-xs text-muted">{t("cogneeReady")}</dt>
            <dd className="mt-1 font-medium text-heading">{availability ? availability.configuration_required ? t("cognee.waitingForConfig") : availability.ready ? t("common:available") : t("common:unavailable") : "—"}</dd></div>
          <div><dt className="text-xs text-muted">{t("cognee.syncWorker")}</dt>
            <dd className="mt-1 font-medium text-heading">{sync ? availability?.configuration_required ? t("cognee.waitingForConfig") : sync.running ? t("cognee.running") : t("cognee.stopped") : "—"}</dd></div>
          <div><dt className="text-xs text-muted">{t("cognee.storage")}</dt>
            <dd className="mt-1 font-medium text-heading">{storage ? formatSize(storage.total_bytes) : "—"}</dd></div>
        </dl>
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
      </Card>

      <StorageDetailsCard status={status} />
      <DatasetMaintenanceCard ready={!!availability?.ready && !statusError} status={status} />

      <Modal open={detail === "models"} onClose={() => setDetail(null)} title={t("cognee.configDialog")} width="max-w-4xl">
        <CogneeSettings status={status} />
      </Modal>
      <Modal open={detail === "recall"} onClose={() => setDetail(null)} title={t("cognee.recallDialog")} width="max-w-4xl">
        <div className="space-y-4"><RecallTester preset="cognee" /><ProbeStatusCard /></div>
      </Modal>
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
