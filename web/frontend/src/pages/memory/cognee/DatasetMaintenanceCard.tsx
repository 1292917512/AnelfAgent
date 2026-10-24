import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { Button, Modal, ConfirmDialog, toast } from "@/components/ui";
import { memoryApi } from "@/lib/api";
import { formatSize } from "@/lib/utils";
import type { CogneeGeneration, CogneeStatus } from "@/lib/types";
import { GenerationCard } from "./GenerationCard";

function retirementBlocker(status?: CogneeStatus): string {
  if (!status?.availability.ready) return "unavailable";
  if (status.retirement?.running) return "running";
  if (status.sync.enabled || status.sync.running || status.sync.configuration_pending || status.sync.processing) return "pause";
  const state = status.projection;
  if (!state || state.rebuild_requested) return "scan";
  if (state.phase === "ready") return status.sync.pending || status.sync.failed ? "pending" : "";
  return ["building", "failed"].includes(state.phase) && state.memory_scanned && state.graph_scanned && state.backup_path ? "" : "scan";
}

export function DatasetMaintenanceCard({ ready, status }: { ready: boolean; status?: CogneeStatus }) {
  const { t } = useTranslation("memory");
  const client = useQueryClient();
  const [helpOpen, setHelpOpen] = useState(false);
  const [target, setTarget] = useState<CogneeGeneration | null>(null);
  const retire = useMutation({
    mutationFn: (namespace: string) => memoryApi.cognee.retireGeneration(namespace),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["cogneeStatus"] }); },
  });
  const { data: catalog, error, refetch } = useQuery({
    queryKey: ["cogneeDatasets"],
    queryFn: () => memoryApi.cognee.datasets().then((r) => r.data),
    refetchInterval: status?.retirement?.running ? 5000 : 30000,
    throwOnError: false,
  });
  const retirementStage = status?.retirement?.stage;
  useEffect(() => {
    if (["completed", "failed", "interrupted"].includes(retirementStage ?? "")) {
      void client.invalidateQueries({ queryKey: ["cogneeDatasets"] });
    }
  }, [client, retirementStage]);
  const enhance = useMutation({
    mutationFn: ({ name, review }: { name: string; review: boolean }) => memoryApi.cognee.improve(name, review),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cogneeStatus"] });
      toast.success(t("cognee.enhanceCompleted"));
    },
  });
  const blocker = retire.isPending ? "running" : !ready || error || !catalog?.ready || catalog.error || catalog.busy
    ? "unavailable" : retirementBlocker(status);
  const generations = catalog?.generations ?? [];
  const generationBlocker = (generation: CogneeGeneration) => blocker || (
    status?.projection?.phase !== "ready" && !generations.some(other => !other.current
      && other.namespace !== generation.namespace && other.datasets.length > 0 && other.document_count !== 0)
      ? "retain" : "");
  const owned = new Set(generations.flatMap(generation => generation.datasets.map(dataset => dataset.id)));
  const external = catalog?.items.filter(dataset => !owned.has(dataset.id)) ?? [];
  return (
    <Card title={t("cognee.datasetsTitle")} subtitle={t("cognee.datasetsSubtitle")}
      actions={<Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)}>{t("cognee.generationHelp")}</Button>}>
      {error && <QueryError error={error} retry={() => void refetch()} compact />}
      {catalog?.error && <p role="alert" className="mb-3 text-sm text-danger">{catalog.error}</p>}
      {catalog?.busy && <p role="status" className="mb-2 text-sm text-accent">{t("cognee.catalogBusy")}</p>}
      {catalog?.updated_at ? <p className="mb-2 text-xs text-muted">{t("cognee.catalogObserved", {
        time: new Date(catalog.updated_at * 1000).toLocaleString(),
      })}</p> : null}
      {catalog?.storage_computed_at ? <p className="mb-2 text-xs text-muted">{t("cognee.generationStorageObserved", {
        time: new Date(catalog.storage_computed_at * 1000).toLocaleString(),
      })}</p> : null}
      {enhance.error && <QueryError error={enhance.error} compact />}
      {retire.error && <QueryError error={retire.error} compact />}
      <Modal open={helpOpen} onClose={() => setHelpOpen(false)} title={t("cognee.generationHelp")}>
        <div className="space-y-4 text-sm leading-relaxed text-muted">
          <p>{t("cognee.retirementHint")}</p><p>{t("cognee.generationMeasurementHint")}</p>
        </div>
      </Modal>
      <div className="grid gap-3 xl:grid-cols-2">
        {generations.map(generation => <GenerationCard key={generation.namespace} generation={generation}
          blocker={generationBlocker(generation)} onRetire={setTarget} onEnhance={(name, review) => enhance.mutate({ name, review })}
          enhanceDisabled={!ready || status?.projection?.phase !== "ready" || !!catalog?.busy || enhance.isPending} />)}
        {!!external.length && <details className="text-sm text-muted">
          <summary className="cursor-pointer">{t("cognee.generationExternal", { count: external.length })}</summary>
          {external.map(dataset => <p key={dataset.id} className="mt-2 break-all text-xs">{dataset.name}</p>)}
        </details>}
        {!error && !catalog?.error && !generations.length && !external.length && <p className="text-sm text-muted">
          {t(catalog?.ready ? "cognee.noDatasets" : "cognee.catalogPending")}
        </p>}
      </div>
      <ConfirmDialog danger open={target !== null} title={t("cognee.retireGeneration")}
        message={t("cognee.retirementGenerationConfirm", {
          name: target?.namespace ?? "", datasets: target?.datasets.length ?? 0,
          documents: target?.document_count ?? t("cognee.unknownCount"),
          size: target?.storage ? formatSize(target.storage.total_bytes) : t("cognee.unknownCount"),
        })}
        onClose={() => setTarget(null)} onConfirm={() => {
          if (!target) return;
          const reason = generationBlocker(target);
          if (reason) { toast.error(t(`cognee.cleanupBlockers.${reason}`)); return; }
          retire.mutate(target.namespace);
          setTarget(null);
        }} />
    </Card>
  );
}
