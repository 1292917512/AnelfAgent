import { useTranslation } from "react-i18next";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Button, Modal } from "@/components/ui";
import type { CogneeProjectionState, CogneeStatus } from "@/lib/types";
import { BatchProgress } from "./BatchProgress";
import { SyncControl } from "./SyncControl";
import { CoverageRepair } from "./CoverageRepair";
import { SyncQueueSummary } from "./SyncQueueSummary";

export function ProjectionStatusCard({ state, sync, updatedAt, stale, enabled, onRetry, retrying }: {
  state?: CogneeProjectionState | null;
  sync?: CogneeStatus["sync"];
  updatedAt: number;
  stale: boolean;
  enabled: boolean;
  onRetry: () => void;
  retrying: boolean;
}) {
  const { t } = useTranslation("memory");
  const [open, setOpen] = useState(false);
  if (!state && !sync) return null;
  const phase = state?.rebuild_requested ? "requested" : state?.phase;
  const total = (state?.counts.memories ?? 0) + (state?.counts.graph_nodes ?? 0);
  const building = !!state && ["scanning", "building", "verifying"].includes(state.phase);
  const hasUpdates = !!sync && (sync.pending > 0 || sync.failed > 0);
  return (
    <Card title={t("cognee.projectionTitle")} subtitle={phase === "ready" && hasUpdates
      ? t("cognee.queue.indexWithUpdates") : phase ? t(`cognee.projectionPhases.${phase}`) : t("cognee.queue.noProjection")}
      actions={<div className="flex flex-wrap gap-2">
        {!!(sync?.failed || sync?.publication?.error) && <Button size="sm" loading={retrying}
          disabled={!enabled || stale} onClick={onRetry}><RefreshCw size={14} />{t("retryFailedSync")}</Button>}
        {state && <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>{t("cognee.buildDetails")}</Button>}
        {sync && <SyncControl sync={sync} enabled={enabled} stale={stale} />}
      </div>}>
      <div className="space-y-3 text-sm text-muted">
        <div className="flex flex-wrap justify-between gap-2 text-xs">
          <span>{t("cognee.progress.refreshHint")}</span>
          {updatedAt > 0 && <span>{t("cognee.progress.refreshed", { time: new Date(updatedAt).toLocaleTimeString() })}</span>}
        </div>
        {stale && <p role="alert" className="text-danger">{t("cognee.progress.stale")}</p>}
        {sync && <SyncQueueSummary sync={sync} />}
        {phase === "requested" && state?.counts.memories === undefined && <p>{t("cognee.progress.inventoryPending")}</p>}
        {building && state && sync && <div className="rounded-lg border border-border bg-elevated p-3 space-y-2">
          <p className="text-base font-semibold text-heading">{t("cognee.progress.expected", { total })}</p>
          <p className="text-xs">{t("cognee.progress.expectedKinds", {
            memories: state.counts.memories ?? 0, relations: state.counts.graph_nodes ?? 0,
          })}</p>
          <p className="text-heading">{t("cognee.progress.overall", { count: sync.synced, total })}</p>
          {total > 0 && sync.synced >= total && <p className="text-accent text-xs" role="status">{t("cognee.progress.awaitingVerification")}</p>}
          <p className="text-xs">{t("cognee.progress.overallHint")}</p>
          {state.phase === "scanning" && <p className="text-xs">{t("cognee.progress.scanningHint")}</p>}
          {state.phase === "verifying" && <p>{t("cognee.progress.verifyingHint")}</p>}
        </div>}
        {sync?.paused && <p role="alert" className="rounded-lg border border-border p-3">
          {t("cognee.progress.paused")}: {sync.pause_reason}
          {!!sync.paused_until && ` (${new Date(sync.paused_until * 1000).toLocaleString()})`}
        </p>}
        {sync && !sync.enabled && <p role="status">{t("cognee.progress.syncPaused")}</p>}
        {sync?.pause_requested && <p role="status" className="text-accent">{t("cognee.progress.pausePending")}</p>}
        {sync?.enabled && !sync.running && sync.pending > 0 && <p role="alert" className="text-danger">{t("cognee.progress.stopped")}</p>}
        {(sync?.retrying ?? 0) > 0 && <p role="status">{t("cognee.progress.retrying", { count: sync?.retrying })}
          {!!sync?.next_retry_at && ` ${t("cognee.progress.retryAfter", { time: new Date(sync.next_retry_at * 1000).toLocaleTimeString() })}`}
        </p>}
        {sync?.configuration_pending && !sync.pause_requested && <p role="status" className="text-accent">{t("cognee.progress.configPending")}</p>}
        {sync?.configuration_error && <p role="alert" className="text-danger break-words">{t("cognee.applyFailed")}: {sync.configuration_error}</p>}
        {sync?.active && <BatchProgress active={sync.active} stale={stale} />}
        {sync?.last_error && <details className="rounded-lg border border-danger/30 bg-danger-subtle p-3">
          <summary className="cursor-pointer text-danger">{t("cognee.lastError")}</summary>
          <p className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap break-words text-xs">{sync.last_error}</p>
        </details>}
        {state?.error && <p role="alert" className="text-danger break-words">{state.error}</p>}
        {state && <CoverageRepair key={state.namespace} state={state} sync={sync} enabled={enabled} stale={stale} />}
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={t("cognee.buildDetails")} width="max-w-2xl">
        {state && <div className="space-y-4 text-sm leading-relaxed text-muted">
          <p className="break-all font-medium text-heading">{state.namespace}</p>
          <p>{t("cognee.projectionHint")}</p>
          {state.completed_at > 0 && <p>{t("cognee.projectionVerified", { count: state.verified })}</p>}
          {(state.legacy_preserved ?? 0) > 0 && <p>{t("cognee.projectionLegacyPreserved", { count: state.legacy_preserved })}</p>}
          {state.previous_namespaces.length > 0 && <p>{t("cognee.projectionRetained", { count: state.previous_namespaces.length })}</p>}
          {building && <p>{t("cognee.progress.restartHint")}</p>}
          {state.counts.memories !== undefined && <p>{t("cognee.projectionInventory", {
            memories: state.counts.memories, relations: state.counts.graph_nodes,
            excluded: state.counts.excluded_memories,
          })}</p>}
          {state.backup_path && <p className="break-all">{t("cognee.projectionBackup")}: {state.backup_path}</p>}
        </div>}
      </Modal>
    </Card>
  );
}
