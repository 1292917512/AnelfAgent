import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Database, Sparkles } from "lucide-react";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { Button, toast } from "@/components/ui";
import { memoryApi } from "@/lib/api";

export function DatasetMaintenanceCard({ ready, namespace }: { ready: boolean; namespace: string }) {
  const { t } = useTranslation("memory");
  const client = useQueryClient();
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
      <div className="space-y-2">
        {datasets?.map((dataset) => {
          const active = [namespace + "_global", namespace + "_relations"].includes(dataset.name);
          return (
            <div key={dataset.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-elevated px-3 py-2">
              <div className="flex min-w-0 items-center gap-2 text-sm">
                <Database size={14} className="shrink-0 text-muted" />
                <span className="break-all text-heading">{dataset.name}</span>
              </div>
              <div className="flex flex-wrap gap-2">
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
    </Card>
  );
}
