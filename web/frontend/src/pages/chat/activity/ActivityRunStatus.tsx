import { AlertCircle, CheckCircle2, Loader2, PauseCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { hasActiveWork } from "@/lib/chat-activity";
import type { ActivityRun } from "@/lib/types/activity";

/** 仅从执行事实区分运行、后台未收束和终态；工具异常不改写轮次结果。 */
export function ActivityRunStatus({ run, runs = [run] }: { run: ActivityRun; runs?: readonly ActivityRun[] }) {
  const { t } = useTranslation("workbench");
  const active = runs.some(hasActiveWork);
  const pending = run.status !== "running" && active;
  const tone = active ? "active" : run.status === "completed" ? "complete" : ["failed", "error"].includes(run.status) ? "error" : "stopped";
  const Icon = active ? Loader2 : tone === "complete" ? CheckCircle2 : tone === "error" ? AlertCircle : PauseCircle;
  return <span className="activity-run-status" data-tone={tone}>
    <Icon size={13} className={active ? "animate-spin" : undefined} aria-hidden="true" />
    {t(pending ? "activity.backgroundWorking" : `activity.status.${run.status}`, { defaultValue: run.status })}
  </span>;
}

/** 明确标记单次执行的结束边界，保留异常与后台工作的真实状态。 */
export function ActivityRunOutcome({ run }: { run: ActivityRun }) {
  const { t } = useTranslation("workbench");
  const active = hasActiveWork(run);
  const issues = run.entries.some((entry) => entry.kind === "tool" && entry.status === "error");
  return <div className="activity-run-outcome" data-status={active ? "pending" : run.status} role="status">
    <div><ActivityRunStatus run={run} /><span className="activity-outcome-detail">{t("activity.operationCount", { count: run.entries.filter((entry) => entry.kind === "tool").length })}</span></div>
    <p>{t(active ? "activity.backgroundWorkingHint" : run.status === "completed" ? "activity.executionComplete" : "activity.executionEnded")}
      {issues && <span className="text-warn"> · {t("activity.needsAttention")}</span>}</p>
    {run.error && <p className="text-warn">{run.error}</p>}
  </div>;
}
