import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { Database, Sparkles } from "lucide-react";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { Button, ConfirmDialog, toast } from "@/components/ui";
import { memoryApi } from "@/lib/api";
import { formatSize } from "@/lib/utils";
import type { CogneeDataset, CogneeStatus } from "@/lib/types";

export function DatasetMaintenanceCard({ ready, namespace, status }: { ready: boolean; namespace: string; status?: CogneeStatus }) {
  const { t } = useTranslation("memory");
  const client = useQueryClient();
  const [target, setTarget] = useState<CogneeDataset | null>(null);
  const retire = useMutation({
    mutationFn: (id: string) => memoryApi.cognee.retireDataset(id),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["cogneeStatus"] }); },
  });
  const { data: datasets, error, refetch } = useQuery({
    queryKey: ["cogneeDatasets"],
    queryFn: () => memoryApi.cognee.datasets().then((r) => r.data),
    enabled: ready,
    refetchInterval: 10000,
    throwOnError: false,
  });
  const enhance = useMutation({
    mutationFn: ({ name, review }: { name: string; review: boolean }) => memoryApi.cognee.improve(name, review),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cogneeStatus"] });
      toast.success(t("cognee.enhanceCompleted"));
    },
  });
  return (
    <Card title={t("cognee.datasetsTitle")} subtitle={t("cognee.datasetsSubtitle")}>
      {error && <QueryError error={error} retry={() => void refetch()} compact />}
      {enhance.error && <QueryError error={enhance.error} compact />}
      {retire.error && <QueryError error={retire.error} compact />}
      <p className="mb-3 text-xs text-muted">{t("cognee.retirementHint")}</p>
      <div className="space-y-2">
        {datasets?.map((dataset) => {
          const active = [namespace + "_global", namespace + "_relations"].includes(dataset.name);
          const state = status?.projection;
          const prefix = state && [state.namespace, ...state.previous_namespaces].sort((a, b) => b.length - a.length)
            .find((name) => dataset.name.startsWith(name + "_"));
          const old = !!prefix && prefix !== state?.namespace;
          const size = status?.storage?.datasets?.[dataset.id];
          const canRetire = old && state?.phase === "ready" && !state.rebuild_requested && !status?.sync.enabled
            && !status?.sync.running && !status?.sync.configuration_pending && !status?.sync.pending && !status?.sync.failed
            && !status?.retirement?.running;
          return (
            <div key={dataset.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-elevated px-3 py-2">
              <div className="min-w-0 space-y-1 text-sm">
                <div className="flex min-w-0 items-center gap-2">
                <Database size={14} className="shrink-0 text-muted" />
                <span className="break-all text-heading">{dataset.name}</span>
                </div>
                <p className="text-xs text-muted">{t(active ? "cognee.datasetCurrent" : old ? "cognee.datasetPrevious" : "cognee.datasetOther")}</p>
                {size ? <p className="text-xs text-muted">{t("cognee.datasetBytes", {
                  graph: formatSize(size.graph_bytes), vectors: formatSize(size.lance_bytes), total: formatSize(size.total_bytes),
                })}</p> : <p className="text-xs text-muted">{t("cognee.datasetBytesUnknown")}</p>}
              </div>
              <div className="flex flex-wrap gap-2">
                {old && <Button size="sm" disabled={!canRetire || retire.isPending} onClick={() => setTarget(dataset)}>
                  {t("cognee.retireDataset")}
                </Button>}
                {[false, true].map((review) => (
                  <Button key={String(review)} size="sm" disabled={!ready || !active || enhance.isPending}
                    onClick={() => enhance.mutate({ name: dataset.name, review })}>
                    <Sparkles size={14} /> {t(review ? "cognee.reviewConflicts" : "cognee.improve")}
                  </Button>
                ))}
              </div>
            </div>
          );
        })}
        {!error && !datasets?.length && <p className="text-sm text-muted">{t("cognee.noDatasets")}</p>}
      </div>
      <ConfirmDialog open={target !== null} title={t("cognee.retireDataset")}
        message={t("cognee.retirementConfirm", { name: target?.name ?? "" })}
        onClose={() => setTarget(null)} onConfirm={() => {
          if (target) retire.mutate(target.id);
          setTarget(null);
        }} />
    </Card>
  );
}
