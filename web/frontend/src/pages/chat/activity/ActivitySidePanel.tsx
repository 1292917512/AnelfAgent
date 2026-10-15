import { useContext, useMemo, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Brain, Gauge, Webhook, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityEntry, ActivityRun } from "@/lib/types/activity";
import { ActivityInspection } from "@/components/common/ActivityInspection";
import { HookJudgmentRow } from "@/components/common/HookJudgmentRow";
import { ActivityEntryView } from "./ActivityEntryView";
import { ActivitySourceLabel } from "./ActivityReferences";

type View = "recollection" | "background" | "judgments";
const views = [{ id: "recollection", icon: Brain }, { id: "background", icon: Webhook }, { id: "judgments", icon: Gauge }] as const;
function matches(entry: ActivityEntry, view: View): boolean {
  if (view === "judgments") return entry.kind === "hook" && !!entry.judgments?.length;
  if (view === "background") return (entry.kind === "hook" && entry.mode === "async") || (entry.kind === "delegation" && entry.background);
  return entry.kind === "context" || (entry.kind === "hook" && (entry.name.startsWith("memory.") || entry.name.startsWith("skills.")));
}

/** 同一活动快照的侧边详情；关闭时不渲染内容，不增加请求或订阅。 */
export function ActivitySidePanel({ runs }: { runs: ActivityRun[] }) {
  const { t } = useTranslation("workbench");
  const [selected, setSelected] = useState<View | null>(null);
  const inspect = useContext(ActivityInspection);
  const groups = useMemo(() => {
    const collect = (view: View) => runs.map((run) => ({ run, entries: run.entries.filter((entry) => matches(entry, view)) })).filter((group) => group.entries.length);
    return { recollection: collect("recollection"), background: collect("background"), judgments: collect("judgments") };
  }, [runs]);
  return <aside className="activity-side-rail" aria-label={t("activity.side.title")}>
    {views.map(({ id, icon: Icon }) => {
      const items = groups[id];
      const entries = items.flatMap((group) => group.entries);
      const running = entries.some((entry) => "status" in entry && entry.status === "running");
      const label = id === "recollection" ? t("activity.recollectionTitle") : t(`activity.side.${id}`);
      return <Popover.Root key={id} open={selected === id} onOpenChange={(open) => setSelected(open ? id : null)}>
        <Popover.Trigger asChild><button type="button" className="activity-side-trigger" aria-label={label} title={label} data-running={running}>
          <Icon size={16} /><span>{entries.length || ""}</span>
        </button></Popover.Trigger>
        <Popover.Portal><Popover.Content side="left" align="start" sideOffset={8} collisionPadding={12} className="activity-side-window" aria-label={label}>
          <header><h3>{label}</h3><Popover.Close asChild><button type="button" aria-label={t("activity.side.close")}><X size={16} /></button></Popover.Close></header>
          <div className="activity-side-body">
            {id === "recollection" && <p className="activity-note">{t("activity.recollectionHint")}</p>}
            {!entries.length && <p className="activity-note">{t("activity.side.empty")}</p>}
            {items.map(({ run, entries: records }) => <section key={run.id} className="space-y-2">
              <div className="activity-side-source"><ActivitySourceLabel source={run.source} /><h4>{run.label || run.actor}</h4></div>
              {records.map((entry) => id === "judgments" && entry.kind === "hook"
                ? <div key={entry.id} className="space-y-2">{entry.judgments?.map((judgment) => <HookJudgmentRow key={judgment.id} judgment={judgment} />)}</div>
                : <ActivityEntryView key={entry.id} entry={entry} current={false} live={run.status === "running"} onReveal={(runId) => inspect?.([runId])} />)}
            </section>)}
          </div>
        </Popover.Content></Popover.Portal>
      </Popover.Root>;
    })}
  </aside>;
}
