import { useContext, useMemo, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { ArrowUpRight, Bot, Brain, Gauge, ListTodo, Webhook, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityRun } from "@/lib/types/activity";
import { formatElapsedCompact } from "@/lib/format";
import { useNow } from "@/hooks/useNow";
import { ActivityInspection } from "@/components/common/ActivityInspection";
import { HookJudgmentRow } from "@/components/common/HookJudgmentRow";
import { ActivityEntryView } from "./ActivityEntryView";
import { ActivitySourceLabel } from "./ActivityReferences";
import { activitySideGroups, type ActivitySideView } from "./activity-side";

const sections = [
  { id: "records", views: [{ id: "recollection", icon: Brain }, { id: "judgments", icon: Gauge }] },
  { id: "running", views: [{ id: "tasks", icon: ListTodo }, { id: "delegates", icon: Bot }, { id: "background", icon: Webhook }] },
] as const;

function RunningExecution({ run }: { run: ActivityRun }) {
  const { t } = useTranslation("workbench");
  const inspect = useContext(ActivityInspection);
  const now = useNow();
  const step = useMemo(() => [...run.entries].reverse().find((entry) => "status" in entry && entry.status === "running"), [run.entries]);
  const title = run.actor || run.label || t(`activity.kinds.${run.kind}`, { defaultValue: run.kind });
  const operation = step?.kind === "tool" || step?.kind === "model" ? step.name : run.label;
  return <button type="button" className="activity-side-execution" onClick={() => inspect?.([run.id])}>
    <span className="activity-live-dot" />
    <span className="activity-side-execution-text"><strong>{title}</strong>{operation && operation !== title && <small>{operation}</small>}</span>
    <span className="activity-duration">{formatElapsedCompact(Math.max(0, now - run.started_at * 1000))}</span><ArrowUpRight size={13} />
  </button>;
}

/** 同一活动快照的侧边详情；关闭时不渲染内容，不增加请求或订阅。 */
export function ActivitySidePanel({ runs, work }: { runs: ActivityRun[]; work: ActivityRun[] }) {
  const { t } = useTranslation("workbench");
  const [selected, setSelected] = useState<ActivitySideView | null>(null);
  const inspect = useContext(ActivityInspection);
  const groups = useMemo(() => activitySideGroups(runs, work), [runs, work]);
  return <aside className="activity-side-rail" aria-label={t("activity.side.title")}>
    {sections.map((section) => <div key={section.id} className="activity-side-section" role="group" aria-label={t(`activity.side.${section.id}`)}>
    {section.views.map(({ id, icon: Icon }) => {
      const items = groups[id];
      const entries = items.flatMap((group) => group.entries);
      const count = id === "judgments" ? entries.reduce((total, entry) => total + (entry.kind === "hook" ? entry.judgments?.length ?? 0 : 0), 0)
        : items.reduce((total, group) => total + Math.max(1, group.entries.length), 0);
      const running = section.id === "running" ? count > 0 : entries.some((entry) => id === "judgments" && entry.kind === "hook"
        ? entry.judgments?.some((judgment) => judgment.status === "running") : "status" in entry && entry.status === "running");
      const label = id === "recollection" ? t("activity.recollectionTitle") : t(`activity.side.${id}`);
      return <Popover.Root key={id} open={selected === id} onOpenChange={(open) => setSelected(open ? id : null)}>
        <Popover.Trigger asChild><button type="button" className="activity-side-trigger" aria-label={label} title={label} data-running={running}>
          <Icon size={16} /><span className="activity-side-label">{label}</span><span className="activity-side-count">{count || ""}</span>
        </button></Popover.Trigger>
        <Popover.Portal><Popover.Content side="left" align="start" sideOffset={8} collisionPadding={12} className="activity-side-window floating-surface" aria-label={label}>
          <header><h3>{label}</h3><Popover.Close asChild><button type="button" aria-label={t("activity.side.close")}><X size={16} /></button></Popover.Close></header>
          <div className="activity-side-body">
            {id === "recollection" && <p className="activity-note">{t("activity.recollectionHint")}</p>}
            {section.id === "running" && count > 0 && <p className="activity-note">{t("activity.side.runningHint")}</p>}
            {!items.length && <p className="activity-note">{t(section.id === "running" ? "activity.side.noRunning" : "activity.side.empty")}</p>}
            {items.map(({ run, entries: records }) => <section key={`${run.id}:${records[0]?.id ?? "run"}`} className="space-y-2">
              <div className="activity-side-source"><ActivitySourceLabel source={run.source} />{records.length > 0 && <h4>{run.label || run.actor}</h4>}</div>
              {!records.length && <RunningExecution run={run} />}
              {records.map((entry) => id === "judgments" && entry.kind === "hook"
                ? <div key={entry.id} className="space-y-2">{entry.judgments?.map((judgment) => <HookJudgmentRow key={judgment.id} judgment={judgment} />)}</div>
                : <ActivityEntryView key={entry.id} entry={entry} current={false} live={run.status === "running"} onReveal={(runId) => inspect?.([runId])} />)}
            </section>)}
          </div>
        </Popover.Content></Popover.Portal>
      </Popover.Root>;
    })}
    </div>)}
  </aside>;
}
