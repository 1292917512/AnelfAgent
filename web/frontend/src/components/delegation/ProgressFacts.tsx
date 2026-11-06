import { useTranslation } from "react-i18next";
import type { DelegationRuntimeProgress } from "@/lib/types";

/** 后端执行事实；提醒不改变运行状态，前端不自行判定超时或失败。 */
export function ProgressFacts({ progress }: { progress: DelegationRuntimeProgress }) {
  const { t } = useTranslation("plan");
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
      {progress.phase && <span>{t(`delegation.phases.${progress.phase}`)}</span>}
      {progress.execution_seconds != null && <span>{t("delegation.executionTime", { n: progress.execution_seconds })}</span>}
      {progress.idle_seconds != null && <span>{t("delegation.lastActivity", { n: progress.idle_seconds })}</span>}
      {progress.overdue && !progress.cancel_requested && (
        <span className="text-amber-600 dark:text-amber-400">{t("delegation.overdue")}</span>
      )}
    </div>
  );
}
