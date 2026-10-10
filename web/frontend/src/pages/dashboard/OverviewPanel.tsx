import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Brain, ChevronDown, Clock3, Inbox, MessageSquare, Server, Wrench } from "lucide-react";
import { useNow } from "@/hooks/useNow";
import { statusApi } from "@/lib/api";
import { SectionBoundary } from "@/components/common/SectionBoundary";
import { QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { useAppStore } from "@/stores/app-store";
import { AttentionPanel } from "./AttentionPanel";
import { ComponentInfoCard } from "./ComponentInfoCard";
import { DelegationsPanel } from "@/components/delegation/DelegationsPanel";
import { ToolsInsightPanel } from "./ToolsInsightPanel";
import { EventsPanel } from "./EventsPanel";
import { ServicesPanel } from "./ServicesPanel";
import { PendingTasks } from "./PendingTasks";

function Uptime() {
  const startedAt = useAppStore((s) => s.startedAt);
  const { t } = useTranslation("dashboard");
  const now = useNow(startedAt !== null);
  const seconds = startedAt === null ? null : Math.max(0, Math.floor(now / 1000 - startedAt));
  const parts: [number, string][] = seconds === null ? [] : [
    [Math.floor(seconds / 86400), "day"], [Math.floor(seconds % 86400 / 3600), "hour"],
    [Math.floor(seconds % 3600 / 60), "minute"], [seconds % 60, "second"],
  ];
  return <span className="overview-uptime"><Clock3 size={14} />{t("uptime")} <strong>{seconds === null ? "—" : parts.filter(([n, unit]) => n > 0 || unit === "second").map(([n, unit]) => `${n}${t(unit)}`).join(" ")}</strong></span>;
}

export function OverviewPanel() {
  const { t } = useTranslation(["dashboard", "status", "common"]);
  const setStartedAt = useAppStore((s) => s.setStartedAt);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const statusQuery = useQuery({ queryKey: ["status"], queryFn: () => statusApi.get().then((r) => r.data), refetchInterval: 3000 });
  const { data: pfc } = useQuery({ queryKey: ["pfc"], queryFn: () => statusApi.pfc().then((r) => r.data), refetchInterval: 3000 });
  const { data: components } = useQuery({ queryKey: ["components"], queryFn: () => statusApi.components().then((r) => r.data), refetchInterval: 10000 });
  const isReady = statusQuery.data?.ready;
  const statusInfo = statusQuery.data?.status as Record<string, unknown> | undefined;
  const phase = String(statusInfo?.mind_phase ?? "idle");
  const pendingTotal = (pfc?.pending_messages?.length ?? 0) + (pfc?.general_tasks?.length ?? 0);
  const tools = components?.structured?.tools;
  const phaseIndex = ["accepting", "recalling"].includes(phase) ? 0 : ["deciding", "introspecting", "llm_calling"].includes(phase) ? 1 : phase === "tool_executing" ? 2 : phase === "replying" ? 3 : -1;
  useEffect(() => {
    if (typeof statusInfo?.uptime === "number" && statusInfo.uptime > 0) setStartedAt(statusInfo.uptime);
  }, [statusInfo?.uptime, setStartedAt]);

  return (
    <div className="overview-layout">
      {statusQuery.error && <QueryError compact error={statusQuery.error} retry={() => void statusQuery.refetch()} />}
      <div className="overview-summary">
        <section className="overview-status" aria-label={t("runningStatus")}>
          <div className="overview-status-copy">
            <div className="flex items-center gap-2 text-xs text-muted"><StatusDot status={statusQuery.isPending ? "offline" : isReady ? "ok" : "danger"} />{statusQuery.isPending ? t("common:loading") : isReady ? t("common:running") : t("common:notReady")}</div>
            <h2>{t(`phaseLabels.${phase}`, { ns: "status", defaultValue: phase })}</h2>
            <Uptime />
          </div>
          <div className="overview-phase-track" aria-label={t("status:thinkingPhase")}>
            {["observe", "think", "execute", "deliver"].map((step, index) => <div key={step} data-active={isReady && phaseIndex === index}>
              <span className="overview-phase-node">{String(index + 1).padStart(2, "0")}</span>
              <span>{t(`overview.${step}`)}</span>
            </div>)}
          </div>
        </section>
        <div className="dashboard-metrics">
          {[
            { label: t("messageCount"), icon: MessageSquare, value: statusInfo?.message_count ?? "—", note: t("overview.received") },
            { label: t("tools"), icon: Wrench, value: tools ? `${tools.enabled} / ${tools.total}` : "—", note: t("overview.availableTools") },
            { label: t("status:stm"), icon: Brain, value: pfc ? `${pfc.short_term_memory_count ?? 0} / ${pfc.short_term_memory_max ?? 0}` : "—", note: t("overview.memoryWindow") },
            { label: t("status:pending"), icon: Inbox, value: pfc ? pendingTotal : "—", note: t("overview.pendingHint") },
          ].map((metric) => <div key={metric.label} className="overview-metric">
            <div><span>{metric.label}</span><metric.icon size={16} /></div>
            <strong>{String(metric.value)}</strong><span className="overview-metric-note">{metric.note}</span>
          </div>)}
        </div>
      </div>
      <div className="overview-inbox">
        <SectionBoundary><AttentionPanel /></SectionBoundary>
        <PendingTasks pfc={pfc} />
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
