import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { QueryError } from "@/components/common/AsyncState";
import { Button } from "@/components/ui";
import { memoryApi } from "@/lib/api";
import type { CogneeProjectionState, CogneeStatus } from "@/lib/types";

export function CoverageRepair({ state, sync, enabled, stale }: {
  state: CogneeProjectionState; sync?: CogneeStatus["sync"]; enabled: boolean; stale: boolean;
}) {
  const { t } = useTranslation("memory");
  const client = useQueryClient();
  const repair = useMutation({
    mutationFn: async () => (await memoryApi.cognee.repairCoverage(state.namespace)).data,
    onSuccess: async () => {
      await Promise.all(["cogneeStatus", "cogneeDatasets", "memoryHealth"].map((key) =>
        client.invalidateQueries({ queryKey: [key] })));
    },
  });
  const eligible = state.phase === "failed" && state.memory_scanned && state.graph_scanned && !state.rebuild_requested;
  if (!eligible && !repair.data) return null;
  const busy = !sync || !!sync.active || sync.configuration_pending;
  return <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 space-y-2" data-testid="cognee-coverage-repair">
    <p className="text-sm">{t("cognee.coverageRepair.hint")}</p>
    {eligible && <Button size="sm" loading={repair.isPending} disabled={!enabled || stale || busy}
      onClick={() => repair.mutate()}>{t("cognee.coverageRepair.action")}</Button>}
    {eligible && busy && <p className="text-xs">{t("cognee.coverageRepair.busy")}</p>}
    {repair.error && <QueryError error={repair.error} compact />}
    {repair.data && <p role="status" className="text-accent">{t("cognee.coverageRepair.queued", { count: repair.data.queued })}
      {" "}{t(repair.data.paused ? "cognee.coverageRepair.paused" : "cognee.coverageRepair.running")}</p>}
  </div>;
}
