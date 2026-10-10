import { useNow } from "@/hooks/useNow";
import { useTranslation } from "react-i18next";
import { AlertCircle, ArrowRight, Brain, Clock, Coins, Wrench } from "lucide-react";
import type { ThinkingSession } from "@/lib/types";
import { cn } from "@/lib/utils";
import { durationLabel, nodeTitle, sessionKind, textValue, traceSummary } from "./trace-model";
import { TraceStages } from "./TraceStages";

export function SessionOverview({ session, onSelect, onSelectSession, compact = false }: {
  session: ThinkingSession; onSelect: (id: string) => void; onSelectSession?: (id: string) => void; compact?: boolean;
}) {
  const { t } = useTranslation("thinking");
  const now = useNow(!session.ended);
  const summary = traceSummary(session);
  const elapsed = session.duration_ms ?? Math.max(0, (session.end_time ? session.end_time * 1000 : now) - session.start_time * 1000);
  const start = session.nodes.find((node) => node.type === "session_start");
  const goal = textValue(start?.data.goal) || textValue(start?.data.scope) || textValue(start?.data.entity);
  const metrics = [
    { icon: Clock, label: t("detailLabels.duration"), value: durationLabel(elapsed) },
    { icon: Brain, label: t("modelCalls"), value: summary.calls.toLocaleString() },
    { icon: Wrench, label: t("toolCalls"), value: summary.tools.toLocaleString() },
    { icon: Coins, label: t("detailLabels.totalTokens"), value: summary.usage.tokens.toLocaleString() },
  ];
  return <section aria-label={t("runOverview")} className={cn("shrink-0 border-b border-border bg-card", compact ? "p-3" : "px-4 py-4 md:px-6")}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs text-muted">
          <span>{t(sessionKind(session))}</span>
          <span className={cn("rounded-full px-2 py-0.5", session.ended ? "bg-elevated text-muted" : "bg-accent-subtle text-accent")}>
            {t(session.outcome ? `outcomes.${session.outcome}` : session.ended ? "runEnded" : "inProgress")}
          </span>
        </div>
        <h2 className={cn("mt-2 font-semibold text-heading break-words", compact ? "text-sm" : "text-base")}>
          {summary.current ? nodeTitle(summary.current, t) : t("waitingForActivity")}
        </h2>
        {goal && <p className="mt-1 truncate text-xs text-muted" title={goal}>{goal}</p>}
        {session.parent_session_id && onSelectSession && <button className="mt-2 break-all text-left text-xs text-accent hover:underline"
          onClick={() => onSelectSession(session.parent_session_id!)}>{t("parentSession", { id: session.parent_session_id })}</button>}
      </div>
      {summary.issues[0] && <button onClick={() => onSelect(summary.issues[0]!.id)} className="inline-flex items-center gap-1.5 rounded-lg bg-warn-subtle px-2.5 py-1.5 text-xs text-warn">
        <AlertCircle size={14} />{t("issueCount", { count: summary.issues.length })}<ArrowRight size={12} />
      </button>}
    </div>
    <dl className="mt-3 grid grid-cols-4 gap-2 border-t border-border pt-3">
      {metrics.map(({ icon: Icon, label, value }) => <div key={label} className="min-w-0">
        <dt className="flex items-center gap-1.5 whitespace-nowrap text-[10px] text-muted sm:text-[11px]"><Icon size={12} className="hidden sm:block" />{label}</dt>
        <dd className={cn("mt-1 font-semibold tabular-nums text-heading", compact ? "text-xs" : "text-sm")}>{value}</dd>
      </div>)}
    </dl>
    {!compact && <TraceStages session={session} onSelect={onSelect} />}
    {session.ended && session.nodes.some((node) => node.status === "running") && <p className="mt-2 text-xs text-warn">{t("incompleteTrace")}</p>}
    {session.nodes_truncated && <p className="mt-2 text-xs text-warn">{t("truncatedTrace")}</p>}
  </section>;
}
