import { memo, useMemo } from "react";
import { Activity, Bot, ChevronDown, ListChecks } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNow } from "@/hooks/useNow";
import { formatElapsedCompact, formatRelativeTimestamp } from "@/lib/format";
import type { ActivityRun } from "@/lib/types/activity";
import { cn } from "@/lib/utils";
import { ActivityEntryView } from "./ActivityEntryView";
import { ActivitySourceLabel, TaggedText } from "./ActivityReferences";
import { SkillEvidence } from "@/components/common/SkillEvidence";
import { activityRows } from "./activity-stream";
import { ActivityRunOutcome, ActivityRunStatus } from "./ActivityRunStatus";
import { ExecutionLimitNotice } from "@/components/activity/ExecutionLimitNotice";

export const ActivityRunCard = memo(function ActivityRunCard({ run, parent, open, current = false, onToggle, onReveal }: { run: ActivityRun; parent?: ActivityRun; current?: boolean; open: boolean; onToggle: (id: string, open: boolean) => void; onReveal: (id: string) => void }) {
  const { t } = useTranslation("workbench");
  const running = run.status === "running";
  const now = useNow(running);
  const title = run.label || t(`activity.kinds.${run.kind}`, { defaultValue: t("activity.background") });
  const tools = run.entries.filter((entry) => entry.kind === "tool");
  const rows = useMemo(() => open ? activityRows(run.entries) : [], [run.entries, open]);
  const hasError = run.status === "failed" || tools.some((entry) => entry.status === "error");
  return <article id={`activity-${run.id}`} className={cn("activity-run", running && "is-running", current && "is-current", run.kind === "delegation" && "is-delegation")} data-activity-run={run.id} data-expanded={open}>
    <div className="activity-run-meta"><span>{t(`activity.kinds.${run.kind}`, { defaultValue: run.kind })}</span><ActivitySourceLabel source={run.source} /><time dateTime={new Date(run.started_at * 1000).toISOString()}>{formatRelativeTimestamp(run.started_at)}</time></div>
    <button className="activity-run-heading" aria-expanded={open} onClick={() => onToggle(run.id, !open)}>
      <span className="activity-run-symbol">{run.kind === "delegation" ? <Bot size={17} /> : run.kind === "task" ? <ListChecks size={17} /> : <Activity size={17} />}</span>
      <span className="min-w-0 flex-1 text-left"><span className="activity-run-title">{title}</span>{run.actor && <span className="activity-run-actor">{run.actor}</span>}</span>
      <span className="activity-duration" title={t(`activity.status.${run.status}`, { defaultValue: run.status })}>{formatElapsedCompact(Math.max(0, (run.ended_at != null ? run.ended_at * 1000 : now) - run.started_at * 1000))}</span>
      <ActivityRunStatus run={run} />
      <ChevronDown size={14} className={cn("text-muted transition-transform", open && "rotate-180")} />
    </button>
    {parent && <button className="activity-segment-parent" onClick={() => onReveal(parent.id)}>{t("activity.belongsTo", { title: parent.label || parent.source.scope })}</button>}
    {open && <SkillEvidence skills={run.skills ?? []} compact />}
    {open ? <div className="activity-run-content">
      {run.input && <details className="activity-input"><summary>{t(run.kind === "conversation" ? "activity.request" : "activity.goal")}</summary><div><TaggedText content={run.input} /></div></details>}
      {run.truncated && <p className="activity-note">{t("activity.truncated")}</p>}
      {run.error && <p className="text-xs text-warn break-words" role="alert">{run.error}</p>}
      {rows.map(({ entry, thoughts }) => <div key={`${entry.id}:${current}`} data-activity-entry={current ? entry.id : undefined}><ActivityEntryView entry={entry} thoughts={thoughts} current={current} live={running && entry.id === run.entries[run.entries.length - 1]?.id} onReveal={onReveal} /></div>)}
      {running && <p className="activity-waiting"><span className="activity-live-dot" />{t(run.entries.some((entry) => entry.kind === "context" && entry.status === "running") ? "activity.contextPreparing" : run.entries.some((entry) => entry.kind === "tool" && entry.status === "running") ? "activity.executing" : "activity.thinking")}</p>}
      {!running && <ActivityRunOutcome run={run} />}
    </div> : <><p className="activity-run-summary">{t("activity.operationCount", { count: tools.length })}{hasError && ` · ${t("activity.needsAttention")}`}</p>
      {run.status === "budget_exhausted" && run.limit && <ExecutionLimitNotice limit={run.limit} />}</>}
  </article>;
});
