import { memo } from "react";
import { Bot, ArrowUpRight, Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityRun } from "@/lib/types/activity";
import { formatElapsedCompact } from "@/lib/format";
import { useNow } from "@/hooks/useNow";
import { SkillEvidence } from "@/components/common/SkillEvidence";
import { ActivitySourceLabel, TaggedText } from "./ActivityReferences";
import { ActivityEntryView } from "./ActivityEntryView";
import type { ActivitySegment } from "./activity-stream";
import { ActivityRunOutcome, ActivityRunStatus } from "./ActivityRunStatus";
import { hasActiveWork } from "@/lib/chat-activity";
import { ActivityRunCard } from "./ActivityRunCard";

function samePresentation(a: ActivityRun, b: ActivityRun): boolean {
  return a.id === b.id && a.status === b.status && a.started_at === b.started_at && a.ended_at === b.ended_at
    && a.actor === b.actor && a.label === b.label && a.kind === b.kind && a.parent_id === b.parent_id
    && a.source === b.source && a.input === b.input && a.skills === b.skills && a.truncated === b.truncated && a.error === b.error && a.limit === b.limit
    && hasActiveWork(a) === hasActiveWork(b);
}

const Heading = memo(function Heading({ run, parent, onReveal }: { run: ActivityRun; parent?: ActivityRun; onReveal: (id: string) => void }) {
  const { t } = useTranslation("workbench");
  const running = run.status === "running";
  const now = useNow(running);
  const title = run.actor || run.label || t(`activity.kinds.${run.kind}`, { defaultValue: run.kind });
  return <header className="activity-segment-header">
      <span className="activity-segment-symbol">{run.parent_id ? <Bot size={15} /> : <Activity size={15} />}</span>
      <div className="min-w-0 flex-1">
        <div className="activity-run-meta"><span>{t(`activity.kinds.${run.kind}`, { defaultValue: run.kind })}</span><ActivitySourceLabel source={run.source} /></div>
        <button className="activity-segment-title" onClick={() => onReveal(run.id)} title={run.label}>{title}<ArrowUpRight size={12} /></button>
        {parent && <button className="activity-segment-parent" onClick={() => onReveal(parent.id)}>{t("activity.belongsTo", { title: parent.label || parent.source.scope })}</button>}
      </div>
      <span className="activity-duration" title={t(`activity.status.${run.status}`, { defaultValue: run.status })}>{formatElapsedCompact(Math.max(0, (run.ended_at != null ? run.ended_at * 1000 : now) - run.started_at * 1000))}</span>
      <ActivityRunStatus run={run} />
    </header>;
}, (a, b) => a.onReveal === b.onReveal && samePresentation(a.run, b.run) && a.parent?.id === b.parent?.id && a.parent?.label === b.parent?.label && a.parent?.source.scope === b.parent?.source.scope);

const Segment = memo(function Segment({ segment, parent, onReveal }: { segment: ActivitySegment; parent?: ActivityRun; onReveal: (id: string) => void }) {
  const { t } = useTranslation("workbench");
  const { run, entries, first, finished } = segment;
  const running = run.status === "running";
  return <article className={`activity-run activity-segment is-current${running ? " is-running" : ""}`} data-activity-run={run.id}>
    <Heading run={run} parent={parent} onReveal={onReveal} />
    <div className="activity-run-content">
      {first && <><SkillEvidence skills={run.skills ?? []} compact />
        {run.input && <details className="activity-input"><summary>{t(run.kind === "conversation" ? "activity.request" : "activity.goal")}</summary><div><TaggedText content={run.input} /></div></details>}
        {run.truncated && <p className="activity-note">{t("activity.truncated")}</p>}</>}
      {entries.map(({ entry, thoughts }) => <div key={entry.id} data-activity-entry={entry.id}><ActivityEntryView entry={entry} thoughts={thoughts} current live={running && entry.id === run.entries[run.entries.length - 1]?.id} onReveal={onReveal} /></div>)}
      {first && entries.length === 0 && running && <p className="activity-waiting">{t("activity.contextPreparing")}</p>}
      {finished && <ActivityRunOutcome run={run} />}
    </div>
  </article>;
}, (previous, next) => previous.onReveal === next.onReveal && previous.parent?.id === next.parent?.id && previous.parent?.label === next.parent?.label
  && previous.segment.run.entries[previous.segment.run.entries.length - 1]?.id === next.segment.run.entries[next.segment.run.entries.length - 1]?.id
  && samePresentation(previous.segment.run, next.segment.run) && previous.segment.first === next.segment.first
  && previous.segment.finished === next.segment.finished && previous.segment.entries.length === next.segment.entries.length
  && previous.segment.entries.every((entry, index) => entry === next.segment.entries[index]));

export function ActivityStream({ runs, segments, onReveal, onToggle }: { runs: ActivityRun[]; segments: ActivitySegment[]; onReveal: (id: string) => void; onToggle: (id: string, open: boolean) => void }) {
  return segments.map((segment) => {
    const parent = runs.find((run) => run.id === segment.run.parent_id);
    return segment.run.kind === "delegation"
      ? <ActivityRunCard key={segment.key} run={segment.run} parent={parent} current open={segment.expanded ?? false} onToggle={onToggle} onReveal={onReveal} />
      : <Segment key={segment.key} segment={segment} parent={parent} onReveal={onReveal} />;
  });
}
