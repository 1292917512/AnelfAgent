import { memo, useMemo } from "react";
import { Bot, ArrowUpRight, Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityRun } from "@/lib/types/activity";
import { formatElapsedCompact } from "@/lib/format";
import { useNow } from "@/hooks/useNow";
import { SkillEvidence } from "@/components/common/SkillEvidence";
import { ActivitySourceLabel, TaggedText } from "./ActivityReferences";
import { ActivityEntryView } from "./ActivityEntryView";
import { activitySegments, type ActivitySegment } from "./activity-stream";

const Segment = memo(function Segment({ segment, parent, onReveal }: { segment: ActivitySegment; parent?: ActivityRun; onReveal: (id: string) => void }) {
  const { t } = useTranslation("workbench");
  const { run, entries, first, finished } = segment;
  const running = run.status === "running";
  const now = useNow(running);
  const title = run.actor || run.label || t(`activity.kinds.${run.kind}`, { defaultValue: run.kind });
  return <article className={`activity-run activity-segment is-current${running ? " is-running" : ""}`} data-activity-run={run.id}>
    <header className="activity-segment-header">
      <span className="activity-segment-symbol">{run.parent_id ? <Bot size={15} /> : <Activity size={15} />}</span>
      <div className="min-w-0 flex-1">
        <div className="activity-run-meta"><span>{t(`activity.kinds.${run.kind}`, { defaultValue: run.kind })}</span><ActivitySourceLabel source={run.source} /></div>
        <button className="activity-segment-title" onClick={() => onReveal(run.id)} title={run.label}>{title}<ArrowUpRight size={12} /></button>
        {parent && <button className="activity-segment-parent" onClick={() => onReveal(parent.id)}>{t("activity.belongsTo", { title: parent.label || parent.source.scope })}</button>}
      </div>
      <span className="activity-duration" title={t(`activity.status.${run.status}`, { defaultValue: run.status })}>{formatElapsedCompact(Math.max(0, (run.ended_at != null ? run.ended_at * 1000 : now) - run.started_at * 1000))}</span>
      {running && <span className="activity-live-dot" role="img" aria-label={t("activity.working")} />}
    </header>
    <div className="activity-run-content">
      {first && <><SkillEvidence skills={run.skills ?? []} compact />
        {run.input && <details className="activity-input"><summary>{t(run.kind === "conversation" ? "activity.request" : "activity.goal")}</summary><div><TaggedText content={run.input} /></div></details>}
        {run.truncated && <p className="activity-note">{t("activity.truncated")}</p>}</>}
      {entries.map(({ entry, thoughts }) => <div key={entry.id} data-activity-entry={entry.id}><ActivityEntryView entry={entry} thoughts={thoughts} current live={running && entry.id === run.entries[run.entries.length - 1]?.id} onReveal={onReveal} /></div>)}
      {first && entries.length === 0 && running && <p className="activity-waiting">{t("activity.contextPreparing")}</p>}
      {finished && <p className="activity-run-footer">{t(`activity.status.${run.status}`, { defaultValue: run.status })} · {t("activity.operationCount", { count: run.entries.filter((entry) => entry.kind === "tool").length })}{run.error && <span className="text-warn"> · {run.error}</span>}</p>}
    </div>
  </article>;
}, (previous, next) => previous.onReveal === next.onReveal && previous.parent === next.parent
  && previous.segment.run === next.segment.run && previous.segment.first === next.segment.first
  && previous.segment.finished === next.segment.finished && previous.segment.entries.length === next.segment.entries.length
  && previous.segment.entries.every((entry, index) => entry === next.segment.entries[index]));

export function ActivityStream({ runs, onReveal }: { runs: ActivityRun[]; onReveal: (id: string) => void }) {
  const segments = useMemo(() => activitySegments(runs), [runs]);
  return segments.map((segment) => <Segment key={segment.key} segment={segment} parent={runs.find((run) => run.id === segment.run.parent_id)} onReveal={onReveal} />);
}
