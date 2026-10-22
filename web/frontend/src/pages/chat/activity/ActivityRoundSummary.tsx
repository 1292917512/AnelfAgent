import { useMemo } from "react";
import { ArrowUpRight, Bot, Brain, ListChecks, MessageCircle, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivitySummaryProps } from "@/lib/ui-contributions";
import { InlineContributions } from "@/components/extensions/ContributionSlot";
import { formatElapsedCompact } from "@/lib/format";
import { useNow } from "@/hooks/useNow";
import { ActivityPlanDetails } from "./ActivityPlan";
import { ActivityInspector } from "./ActivityInspector";
import { ActivitySourceLabel } from "./ActivityReferences";
import { activitySummary, type ActivityDelegate } from "./activity-summary";

function DelegateItem({ item, onReveal }: { item: ActivityDelegate; onReveal: (id: string) => void }) {
  const { t } = useTranslation("workbench");
  const latest = item.runs[item.runs.length - 1];
  const status = item.entry?.status || latest?.status || "queued";
  const active = status === "running" || status === "queued";
  const now = useNow(active);
  const started = item.entry?.ts ?? item.runs[0]?.started_at ?? 0;
  const duration = active ? now - started * 1000 : item.entry?.duration_ms
    ?? (latest ? ((latest.ended_at ?? latest.updated_at) - started) * 1000 : 0);
  return <li className="activity-summary-delegate">
    <div className="flex items-center gap-2 text-sm"><Bot size={15} className="text-accent" /><strong>{item.entry?.agent || latest?.actor || t("activity.kinds.delegation")}</strong>
      <span className="ml-auto text-xs text-muted">{t(`activity.status.${status}`, { defaultValue: status })} · {formatElapsedCompact(Math.max(0, duration))}</span></div>
    <p>{item.entry?.goal || latest?.label}</p>
    <small>{t("activity.belongsTo", { title: item.parent.label || item.parent.id })}</small>
    <div className="flex flex-wrap gap-2">{item.runs.map((run, index) => <button key={run.id} className="activity-run-link" onClick={() => onReveal(run.id)}>
      <ArrowUpRight size={13} />{t("activity.viewExecution")}{item.runs.length > 1 && ` ${index + 1}`}
    </button>)}{!item.runs.length && <span className="activity-note">{t("activity.summary.awaitingRun")}</span>}</div>
  </li>;
}

/** 当前轮次的来源与轻量摘要；完整子代理和计划仅在详情打开后渲染。 */
export function ActivityRoundSummary({ run, runs, onReveal }: ActivitySummaryProps) {
  const { t } = useTranslation("workbench");
  const summary = useMemo(() => activitySummary(run, runs), [run, runs]);
  const title = run.label || t(`activity.kinds.${run.kind}`, { defaultValue: run.kind });
  const kind = t(`activity.kinds.${run.kind}`, { defaultValue: run.kind });
  const trigger = run.trigger && run.trigger !== "chat" ? t(`activity.triggers.${run.trigger}`, {
    defaultValue: t(`settings:hooks.events.${run.trigger}`, { defaultValue: run.trigger }),
  }) : kind;
  const sender = run.initiator?.name || run.initiator?.id || (run.kind === "conversation" ? run.source.target : "");
  const Icon = run.kind === "conversation" ? MessageCircle : run.kind === "delegation" ? Bot : run.kind === "reflection" ? Brain : Zap;
  return <div className="activity-round-summary" data-summary-run={run.id}>
    <button className="activity-round-focus" onClick={() => onReveal(run.id)} title={`${trigger} · ${title}`}>
      <Icon size={16} className="shrink-0 text-accent" />
      <span className="min-w-0"><span className="activity-round-origin">{trigger}{run.source.channel && ` · ${run.source.channel}`}{sender && <span title={run.initiator?.id}>{` · ${sender}`}</span>}</span>
        <strong>{title}</strong></span>
    </button>
    <div className="activity-summary-actions">
      {summary.delegates.length > 0 && <ActivityInspector title={t("activity.summary.delegates")} preview={t("activity.summary.delegateCount", { count: summary.delegates.length })}
        className="activity-summary-action" trigger={<><Bot size={15} /><span>{summary.delegates.length}</span></>}>
        <p className="activity-summary-parent">{title}</p><ActivitySourceLabel source={run.source} />
        <ul className="activity-summary-list">{summary.delegates.map((item) => <DelegateItem key={item.id} item={item} onReveal={onReveal} />)}</ul>
      </ActivityInspector>}
      {summary.plans.length > 0 && <ActivityInspector title={t("activity.summary.plans")} preview={t("activity.summary.planProgress", { done: summary.done, total: summary.steps })}
        className="activity-summary-action" trigger={<><ListChecks size={15} /><span>{summary.done}/{summary.steps}</span></>}>
        <p className="activity-summary-parent">{title}</p>
        <div className="space-y-4">{summary.plans.map(({ run: owner, entry }) => <section key={`${owner.id}:${entry.id}`}>
          {owner.id !== run.id && <p className="activity-note">{t("activity.belongsTo", { title: owner.actor || owner.label })}</p>}
          <h3>{entry.goal}</h3><ActivityPlanDetails entry={entry} />
        </section>)}</div>
      </ActivityInspector>}
      <InlineContributions slot="activity.summary.actions" componentProps={{ run, runs, onReveal }} />
    </div>
  </div>;
}
