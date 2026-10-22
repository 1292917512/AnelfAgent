import { useTranslation } from "react-i18next";
import { useState } from "react";
import { Card } from "@/components/common/Card";
import { Button, Modal } from "@/components/ui";
import type { CogneeProjectionState, CogneeStatus } from "@/lib/types";
import { BatchProgress } from "./BatchProgress";
import { SyncControl } from "./SyncControl";

export function ProjectionStatusCard({ state, sync, updatedAt, stale, enabled }: {
  state?: CogneeProjectionState | null;
  sync?: CogneeStatus["sync"];
  updatedAt: number;
  stale: boolean;
  enabled: boolean;
}) {
  const { t } = useTranslation("memory");
  const [open, setOpen] = useState(false);
  if (!state) return null;
  const phase = state.rebuild_requested ? "requested" : state.phase;
  const total = (state.counts.memories ?? 0) + (state.counts.graph_nodes ?? 0);
  const building = ["scanning", "building", "verifying"].includes(state.phase);
  return (
    <Card title={t("cognee.projectionTitle")} subtitle={t(`cognee.projectionPhases.${phase}`)}
      actions={<div className="flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>{t("cognee.buildDetails")}</Button>
        {sync && <SyncControl sync={sync} enabled={enabled} stale={stale} />}
      </div>}>
      <div className="space-y-3 text-sm text-muted">
        <div className="flex flex-wrap justify-between gap-2 text-xs">
          <span>{t("cognee.progress.refreshHint")}</span>
          {updatedAt > 0 && <span>{t("cognee.progress.refreshed", { time: new Date(updatedAt).toLocaleTimeString() })}</span>}
        </div>
        {stale && <p role="alert" className="text-danger">{t("cognee.progress.stale")}</p>}
        {phase === "requested" && state.counts.memories === undefined && <p>{t("cognee.progress.inventoryPending")}</p>}
        {building && sync && <div className="rounded-lg border border-border bg-elevated p-3 space-y-2">
          <p className="text-base font-semibold text-heading">{t("cognee.progress.expected", { total })}</p>
          <p className="text-xs">{t("cognee.progress.expectedKinds", {
            memories: state.counts.memories ?? 0, relations: state.counts.graph_nodes ?? 0,
          })}</p>
          <p className="text-heading">{t("cognee.progress.overall", { count: sync.synced, total })}</p>
          {total > 0 && <progress className="w-full h-2 accent-accent" aria-label={t("cognee.progress.overallLabel")}
            value={Math.min(sync.synced, total)} max={total} />}
          <p className="text-xs">{t("cognee.progress.overallHint")}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span>{t("cognee.progress.queued", { count: Math.max(0, sync.pending - (sync.processing ?? 0)) })}</span>
            <span>{t("cognee.progress.processing", { count: sync.processing ?? 0 })}</span>
            <span className={sync.failed ? "text-danger" : ""}>{t("cognee.progress.failed", { count: sync.failed })}</span>
          </div>
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
        {sync?.active && <BatchProgress active={sync.active} stale={stale} />}
        {sync?.last_error && <p role="alert" className="text-danger break-words">{t("cognee.lastError")}: {sync.last_error}</p>}
        {state.completed_at > 0 && <p>{t("cognee.projectionVerified", { count: state.verified })}</p>}
        {(state.legacy_preserved ?? 0) > 0 && <p>{t("cognee.projectionLegacyPreserved", { count: state.legacy_preserved })}</p>}
        {state.previous_namespaces.length > 0 && <p>{t("cognee.projectionRetained", { count: state.previous_namespaces.length })}</p>}
        {state.error && <p role="alert" className="text-danger">{state.error}</p>}
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={t("cognee.buildDetails")} width="max-w-2xl">
        <div className="space-y-4 text-sm leading-relaxed text-muted">
          <p className="break-all font-medium text-heading">{state.namespace}</p>
          <p>{t("cognee.projectionHint")}</p>
          {building && <p>{t("cognee.progress.restartHint")}</p>}
          {state.counts.memories !== undefined && <p>{t("cognee.projectionInventory", {
            memories: state.counts.memories, relations: state.counts.graph_nodes,
            excluded: state.counts.excluded_memories,
          })}</p>}
          {state.backup_path && <p className="break-all">{t("cognee.projectionBackup")}: {state.backup_path}</p>}
        </div>
      </Modal>
    </Card>
  );
}
