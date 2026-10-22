import { ArrowUpRight, Check, ListChecks, Loader2 } from "lucide-react";
import type { ActivityEntry } from "@/lib/types/activity";
import { PlanMeta } from "@/components/plan/PlanMeta";
import { ActivityInspector } from "./ActivityInspector";
import { TaggedText } from "./ActivityReferences";

type PlanEntry = Extract<ActivityEntry, { kind: "plan" }>;

/** 过程行与轮次汇总共用计划详情，风险和步骤只在查看时挂载。 */
export function ActivityPlanDetails({ entry }: { entry: PlanEntry }) {
  return <>
    <ol className="activity-plan-steps">{entry.steps.map((step, index) => <li key={index} data-status={step.status}>
      {step.status === "completed" ? <Check size={14} /> : step.status === "in_progress" && entry.status === "running"
        ? <Loader2 size={14} className="animate-spin text-accent" /> : <span className="activity-step-number">{index + 1}</span>}
      <div><TaggedText content={step.content} />{step.note && <p>{step.note}</p>}</div>
    </li>)}</ol>
    {(entry.files || entry.risks) && <PlanMeta plan={{ files: entry.files ?? "", risks: entry.risks ?? "" }} />}
  </>;
}

export function ActivityPlan({ entry }: { entry: PlanEntry }) {
  const done = entry.steps.filter((step) => step.status === "completed").length;
  const current = entry.steps.find((step) => step.status === "in_progress");
  return <div className="activity-plan">
    <ActivityInspector title={entry.goal} preview={current?.content ?? entry.goal} className="activity-tool-trigger" trigger={<>
      <ListChecks size={15} className="text-accent" /><span className="min-w-0 flex-1 truncate">{entry.goal}</span>
      <span className="text-xs tabular-nums text-muted">{done}/{entry.steps.length}</span><ArrowUpRight size={14} className="shrink-0 text-muted" />
    </>}><ActivityPlanDetails entry={entry} /></ActivityInspector>
    {current && <p className="activity-compact-preview"><TaggedText content={current.content} /></p>}
  </div>;
}
