import { memo } from "react";
import { Activity, Bot, ChevronDown, Check, AlertCircle, Pause, ListChecks } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNow } from "@/hooks/useNow";
import { formatElapsedCompact, formatRelativeTimestamp } from "@/lib/format";
import type { ActivityRun } from "@/lib/types/activity";
import { cn } from "@/lib/utils";
import { ActivityEntryView } from "./ActivityEntryView";
import { ActivitySourceLabel, TaggedText } from "./ActivityReferences";

export const ActivityRunCard = memo(function ActivityRunCard({ run, open, onToggle, onReveal }: { run: ActivityRun; open: boolean; onToggle: (id: string, open: boolean) => void; onReveal: (id: string) => void }) {
  const { t } = useTranslation("workbench");
  const running = run.status === "running";
  const now = useNow(running);
  const title = run.label || t(`activity.kinds.${run.kind}`, { defaultValue: t("activity.background") });
  const tools = run.entries.filter((entry) => entry.kind === "tool");
  const hasError = run.status === "failed" || tools.some((entry) => entry.status === "error");
  const stopped = ["cancelled", "interrupted", "budget_exhausted"].includes(run.status);
  return <article id={`activity-${run.id}`} className={cn("activity-run", running && "is-running", run.kind === "delegation" && "is-delegation")} data-activity-run={run.id}>
    <div className="activity-run-meta"><span>{t(`activity.kinds.${run.kind}`, { defaultValue: run.kind })}</span><ActivitySourceLabel source={run.source} /><time dateTime={new Date(run.started_at * 1000).toISOString()}>{formatRelativeTimestamp(run.started_at)}</time></div>
    <button className="activity-run-heading" aria-expanded={open} onClick={() => onToggle(run.id, !open)}>
      <span className="activity-run-symbol">{run.kind === "delegation" ? <Bot size={17} /> : run.kind === "task" ? <ListChecks size={17} /> : <Activity size={17} />}</span>
      <span className="min-w-0 flex-1 text-left"><span className="activity-run-title">{title}</span>{run.actor && <span className="activity-run-actor">{run.actor}</span>}</span>
      <span className="activity-duration" title={t(`activity.status.${run.status}`, { defaultValue: run.status })}>{formatElapsedCompact(Math.max(0, (run.ended_at != null ? run.ended_at * 1000 : now) - run.started_at * 1000))}</span>
      {running ? <span className="activity-live-dot" aria-label={t("activity.working")} /> : hasError ? <AlertCircle size={14} className="text-warn" aria-label={t("activity.needsAttention")} /> : stopped ? <Pause size={14} className="text-muted" aria-label={t("activity.interrupted")} /> : <Check size={14} className="text-muted" aria-label={t("activity.status.completed")} />}
      <ChevronDown size={14} className={cn("text-muted transition-transform", open && "rotate-180")} />
    </button>
    {open ? <div className="activity-run-content" onClickCapture={() => onToggle(run.id, true)}>
      {run.input && <details className="activity-input"><summary>{t(run.kind === "conversation" ? "activity.request" : "activity.goal")}</summary><div><TaggedText content={run.input} /></div></details>}
      {run.truncated && <p className="activity-note">{t("activity.truncated")}</p>}
      {run.error && <p className="text-xs text-warn break-words" role="alert">{run.error}</p>}
      {run.entries.map((entry, index) => <ActivityEntryView key={entry.id} entry={entry} live={running && index === run.entries.length - 1} onReveal={onReveal} />)}
      {running && <p className="activity-waiting"><span className="activity-live-dot" />{t(run.entries.some((entry) => entry.kind === "context" && entry.status === "running") ? "activity.contextPreparing" : run.entries.some((entry) => entry.kind === "tool" && entry.status === "running") ? "activity.executing" : "activity.thinking")}</p>}
      {!running && <p className="activity-run-footer">{t(`activity.status.${run.status}`, { defaultValue: run.status })} · {t("activity.operationCount", { count: tools.length })}{hasError && ` · ${t("activity.needsAttention")}`}</p>}
    </div> : <p className="activity-run-summary">{t("activity.operationCount", { count: tools.length })}{hasError && ` · ${t("activity.needsAttention")}`}</p>}
  </article>;
});
