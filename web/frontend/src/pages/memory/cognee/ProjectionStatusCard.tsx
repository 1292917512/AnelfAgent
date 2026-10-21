import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import type { CogneeProjectionState } from "@/lib/types";

export function ProjectionStatusCard({ state }: { state?: CogneeProjectionState | null }) {
  const { t } = useTranslation("memory");
  if (!state) return null;
  const phase = state.rebuild_requested ? "requested" : state.phase;
  return (
    <Card title={t("cognee.projectionTitle")} subtitle={t(`cognee.projectionPhases.${phase}`)}>
      <div className="space-y-2 text-sm text-muted" role="status">
        <p>{t("cognee.projectionHint")}</p>
        {state.completed_at > 0 && <p>{t("cognee.projectionVerified", { count: state.verified })}</p>}
        {(state.legacy_preserved ?? 0) > 0 && <p>{t("cognee.projectionLegacyPreserved", { count: state.legacy_preserved })}</p>}
        {state.counts.memories !== undefined && <p>{t("cognee.projectionInventory", {
          memories: state.counts.memories, relations: state.counts.graph_nodes,
          excluded: state.counts.excluded_memories,
        })}</p>}
        {state.backup_path && <p className="break-all">{t("cognee.projectionBackup")}: {state.backup_path}</p>}
        {state.previous_namespaces.length > 0 && <p>{t("cognee.projectionRetained", { count: state.previous_namespaces.length })}</p>}
        {state.error && <p role="alert" className="text-danger">{state.error}</p>}
      </div>
    </Card>
  );
}
