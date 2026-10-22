import { useTranslation } from "react-i18next";
import type { CogneeBatchProgress } from "@/lib/types";

function duration(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 3600).toString().padStart(2, "0")}:${Math.floor(whole / 60 % 60).toString().padStart(2, "0")}:${(whole % 60).toString().padStart(2, "0")}`;
}

export function BatchProgress({ active, stale }: { active: CogneeBatchProgress; stale: boolean }) {
  const { t } = useTranslation("memory");
  const pipeline = active.pipeline;
  const total = pipeline?.total_items;
  const completed = pipeline?.completed_items;
  const hasCount = typeof total === "number" && total > 0 && typeof completed === "number";
  return <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 space-y-2" data-testid="cognee-batch-progress">
    <div className="flex flex-wrap justify-between gap-2 text-heading">
      <span>{t("cognee.progress.batch", { count: active.items, attempt: active.attempt })}</span>
      <span>{t(`cognee.progress.stages.${active.stage}`, { defaultValue: active.stage })}</span>
    </div>
    <p>{t("cognee.progress.elapsed", { time: duration(active.elapsed_seconds) })}</p>
    {active.timeout_seconds > 0 && <p className="text-xs">{t("cognee.progress.deadline", {
      elapsed: duration(active.stage_elapsed_seconds), limit: duration(active.timeout_seconds),
    })}</p>}
    {hasCount ? <>
      <p className="text-heading">{t("cognee.progress.pipelineCount", { count: completed, total })}</p>
      <progress className="w-full h-2 accent-accent" aria-label={t("cognee.progress.pipelineLabel")}
        value={Math.min(completed, total)} max={total} />
      <p className="text-xs">{t("cognee.progress.pipelineHint")}</p>
    </> : active.stage === "cognify" && <p>{t("cognee.progress.waitingReport")}</p>}
    {pipeline?.current_stage && <p className="text-xs">{t("cognee.progress.reportedStage", {
      stage: t(`cognee.progress.pipelineStages.${pipeline.current_stage}`, { defaultValue: pipeline.current_stage }),
    })}</p>}
    <p className="text-xs">{t("cognee.progress.lastChange", { time: new Date(active.last_change_at * 1000).toLocaleTimeString(),
      duration: duration(active.quiet_seconds) })}</p>
    {active.progress_error && <p role="alert" className="text-danger break-words">{t("cognee.progress.readError")}: {active.progress_error}</p>}
    {!stale && active.quiet_seconds >= 300 && <p role="status">{t("cognee.progress.quietHint")}</p>}
  </div>;
}
