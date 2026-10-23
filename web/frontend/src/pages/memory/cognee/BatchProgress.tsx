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
  const documents = active.documents;
  const hasFragments = !!documents && documents.fragments > 0;
  const observed = documents && typeof documents.completed === "number";
  const unknown = t("cognee.progress.notObserved");
  return <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 space-y-2" data-testid="cognee-batch-progress">
    <div className="flex flex-wrap justify-between gap-2 text-heading">
      <span>{t("cognee.progress.batch", { count: active.items, attempt: active.attempt })}</span>
      <span>{t(`cognee.progress.stages.${active.stage}`, { defaultValue: active.stage })}</span>
    </div>
    <dl className={`grid grid-cols-1 ${hasFragments ? "sm:grid-cols-3" : "sm:grid-cols-2"} gap-3 rounded-lg bg-surface/60 p-3`}>
      <div><dt className="text-xs text-muted">{t("cognee.progress.actualDocuments")}</dt>
        <dd className="mt-1 text-xl font-semibold text-heading tabular-nums">{documents ? `${documents.completed ?? "—"} / ${documents.total}` : unknown}</dd></div>
      {hasFragments && <div><dt className="text-xs text-muted">{t("cognee.progress.actualFragments")}</dt>
        <dd className="mt-1 text-xl font-semibold text-heading tabular-nums">{`${documents.fragments_completed ?? "—"} / ${documents.fragments}`}</dd></div>}
      <div><dt className="text-xs text-muted">{t("cognee.progress.lastCompleted")}</dt>
        <dd className="mt-1 font-medium text-heading">{documents?.last_completed_at
          ? new Date(documents.last_completed_at * 1000).toLocaleTimeString() : t("cognee.progress.noCompletion")}</dd>
        <dd className="mt-1 text-xs">{t("cognee.progress.elapsed", { time: duration(active.elapsed_seconds) })}</dd></div>
    </dl>
    {observed && documents.total > 0 && <progress className="w-full h-2 accent-accent"
      aria-label={t("cognee.progress.actualDocuments")} value={documents.completed ?? 0} max={documents.total} />}
    <p className="text-xs">{t("cognee.progress.actualHint")}</p>
    {active.stage === "cognify" && observed && documents.completed === documents.total &&
      <p role="status" className="text-accent">{t("cognee.progress.awaitingCommit")}</p>}
    {active.stage === "cognify" && !observed && <p>{t("cognee.progress.waitingActual")}</p>}
    {!!active.documents_checked_at && <p className="text-xs">{t("cognee.progress.actualChecked", {
      time: new Date(active.documents_checked_at * 1000).toLocaleTimeString(),
    })}</p>}
    <details className="border-t border-border pt-2">
      <summary className="cursor-pointer text-xs text-muted">{t("cognee.progress.diagnostics")}</summary>
      <div className="mt-3 space-y-2 text-xs">
        {active.timeout_seconds > 0 && <p>{t("cognee.progress.deadline", {
          elapsed: duration(active.stage_elapsed_seconds), limit: duration(active.timeout_seconds),
        })}</p>}
        {hasCount && <p>{t("cognee.progress.pipelineCount", { count: completed, total })}</p>}
        <p>{t("cognee.progress.pipelineHint")}</p>
        {pipeline?.current_stage && <p>{t("cognee.progress.reportedStage", {
          stage: t(`cognee.progress.pipelineStages.${pipeline.current_stage}`, { defaultValue: pipeline.current_stage }),
        })}</p>}
        <p>{t("cognee.progress.lastChange", { time: new Date(active.last_change_at * 1000).toLocaleTimeString(),
          duration: duration(active.quiet_seconds) })}</p>
      </div>
    </details>
    {active.progress_error && <p role="alert" className="text-danger break-words">{t("cognee.progress.readError")}: {active.progress_error}</p>}
    {!stale && !active.progress_error && active.stage === "cognify" && observed &&
      documents.completed !== documents.total && active.quiet_seconds >= 300 &&
      <p role="status">{t("cognee.progress.quietHint")}</p>}
  </div>;
}
