import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Brain, ChevronDown, Clock3, Eye, Inbox, MessageSquare, Send, Server, Wrench } from "lucide-react";
import { useNow } from "@/hooks/useNow";
import { runtimeComponentsQuery, runtimeStatusQuery, runtimeThinkingQuery } from "@/lib/runtime-queries";
import { SectionBoundary } from "@/components/common/SectionBoundary";
import { QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { AttentionPanel } from "./AttentionPanel";
import { ComponentInfoCard } from "./ComponentInfoCard";
import { DelegationsPanel } from "@/components/delegation/DelegationsPanel";
import { ToolsInsightPanel } from "./ToolsInsightPanel";
import { EventsPanel } from "./EventsPanel";
import { ServicesPanel } from "./ServicesPanel";
import { PendingTasks } from "./PendingTasks";

const PHASE_STEPS = [
  { key: "observe", icon: Eye, phases: ["accepting", "recalling"] },
  { key: "think", icon: Brain, phases: ["deciding", "introspecting", "llm_calling"] },
  { key: "execute", icon: Wrench, phases: ["tool_executing"] },
  { key: "deliver", icon: Send, phases: ["replying"] },
];

function Uptime({ uptime, observedAt }: { uptime?: number; observedAt: number }) {
  const { t } = useTranslation("dashboard");
  const now = useNow(uptime !== undefined);
  const seconds = uptime === undefined ? null : Math.floor(uptime + Math.max(0, now - observedAt) / 1000);
  const parts: [number, string][] = seconds === null ? [] : [
    [Math.floor(seconds / 86400), "day"], [Math.floor(seconds % 86400 / 3600), "hour"],
    [Math.floor(seconds % 3600 / 60), "minute"], [seconds % 60, "second"],
  ];
  return <span className="overview-uptime"><Clock3 size={14} />{t("uptime")} <strong>{seconds === null ? "—" : parts.filter(([n, unit]) => n > 0 || unit === "second").map(([n, unit]) => `${n}${t(unit)}`).join(" ")}</strong></span>;
}

export function OverviewPanel() {
  const { t } = useTranslation(["dashboard", "status", "common"]);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const statusQuery = useQuery(runtimeStatusQuery());
  const thinkingQuery = useQuery({ ...runtimeThinkingQuery(), refetchInterval: statusQuery.data?.status?.mind_phase === "idle" ? 10_000 : 3000 });
  const components = useQuery({ ...runtimeComponentsQuery(), refetchInterval: 10_000 });
  const statusInfo = statusQuery.isSuccess ? statusQuery.data.status : undefined;
  const phase = statusInfo?.mind_phase;
  const thinking = thinkingQuery.isSuccess ? thinkingQuery.data : null;
  const pendingTotal = thinking?.pending_messages && thinking.general_tasks ? thinking.pending_messages.length + thinking.general_tasks.length : "—";
  const tools = components.isSuccess ? components.data.structured?.tools : undefined;
  const health = statusQuery.isPending ? "loading" : statusQuery.isError ? "serviceUnavailable" : statusQuery.data?.ready ? "running" : "notReady";

  return (
    <div className="overview-layout">
      {statusQuery.error && <QueryError compact error={statusQuery.error} retry={() => void statusQuery.refetch()} />}
      {components.error && <QueryError compact title={t("overview.componentsFailed")} error={components.error} retry={() => void components.refetch()} />}
      <div className="overview-summary">
        <section className="overview-status" aria-label={t("runningStatus")}>
          <div className="overview-status-copy">
            <div className="flex items-center gap-2 text-xs text-muted"><StatusDot status={health === "running" ? "ok" : statusQuery.isError ? "danger" : "offline"} />{t(`common:${health}`)}</div>
            <h2>{phase ? t(`phaseLabels.${phase}`, { ns: "status", defaultValue: phase }) : "—"}</h2>
            <Uptime uptime={statusInfo?.uptime} observedAt={statusQuery.dataUpdatedAt} />
          </div>
          <ol className="overview-phase-track" aria-label={t("status:thinkingPhase")}>
            {PHASE_STEPS.map(({ key, icon: Icon, phases }) => {
              const active = health === "running" && !!phase && phases.includes(phase);
              return <li key={key} data-active={active} aria-current={active ? "step" : undefined}>
                <span className="overview-phase-node"><Icon size={16} strokeWidth={1.7} aria-hidden="true" /></span>
                <span>{t(`overview.${key}`)}</span>
              </li>;
            })}
          </ol>
        </section>
        <div className="dashboard-metrics">
          {[
            { label: t("messageCount"), icon: MessageSquare, value: statusInfo?.message_count ?? "—", note: t("overview.received") },
            { label: t("tools"), icon: Wrench, value: tools ? `${tools.enabled} / ${tools.total}` : "—", note: t("overview.availableTools") },
            { label: t("status:stm"), icon: Brain, value: thinking?.short_term_memory_count != null ? `${thinking.short_term_memory_count} / ${thinking.short_term_memory_max ?? "—"}` : "—", note: t("overview.memoryWindow") },
            { label: t("status:pending"), icon: Inbox, value: pendingTotal, note: t("overview.pendingHint") },
          ].map((metric) => <div key={metric.label} className="overview-metric">
            <div><span>{metric.label}</span><metric.icon size={16} /></div>
            <strong>{String(metric.value)}</strong><span className="overview-metric-note">{metric.note}</span>
          </div>)}
        </div>
      </div>
      <div className="overview-inbox">
        <SectionBoundary><AttentionPanel /></SectionBoundary>
        <PendingTasks snapshot={thinking} loading={thinkingQuery.isPending} error={thinkingQuery.error} retry={() => void thinkingQuery.refetch()} />
      </div>
      <SectionBoundary><DelegationsPanel /></SectionBoundary>
      <Card className="overview-details !p-0">
        <button type="button" className="overview-details-toggle" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(!detailsOpen)}>
          <Server size={17} /><span><strong>{t("overview.systemDetails")}</strong><small>{t("overview.systemDetailsHint")}</small></span><ChevronDown size={17} className={detailsOpen ? "rotate-180" : ""} />
        </button>
        {detailsOpen && <div className="overview-details-content">
          <SectionBoundary><ComponentInfoCard /></SectionBoundary>
          <SectionBoundary><ToolsInsightPanel /></SectionBoundary>
          <SectionBoundary><ServicesPanel /></SectionBoundary>
          <SectionBoundary><EventsPanel /></SectionBoundary>
        </div>}
      </Card>
    </div>
  );
}
