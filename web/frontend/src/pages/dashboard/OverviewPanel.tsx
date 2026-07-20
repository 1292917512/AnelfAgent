import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { statusApi, toolsApi } from "@/lib/api";
import { StatCard } from "@/components/common/StatCard";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { useAppStore } from "@/stores/app-store";
import { Activity, MessageCircle, Wrench, Brain } from "lucide-react";

type StmItem = { index: number; role: string; content: string };

type PfcSnapshot = {
  tool_recall: { name: string; count: number }[];
  tool_recall_top_n: number;
  tag_activated_tools: string[];
  pending_messages: { scope: string; preview: string; adapter_key: string }[];
  general_tasks: { type: string; scope: string; preview: string }[];
  pending_analysis_count: number;
  short_term_memory_count: number;
  short_term_memory_max: number;
  short_term_memory_items?: StmItem[];
  active_tools?: string[];
};

function formatUptime(seconds: number, t: (key: string) => string): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}${t("day")} ${h}${t("hour")} ${m}${t("minute")} ${s}${t("second")}`;
  if (h > 0) return `${h}${t("hour")} ${m}${t("minute")} ${s}${t("second")}`;
  if (m > 0) return `${m}${t("minute")} ${s}${t("second")}`;
  return `${s}${t("second")}`;
}

function useUptime() {
  const startedAt = useAppStore((s) => s.startedAt);
  const { t } = useTranslation("dashboard");
  const [display, setDisplay] = useState("—");

  useEffect(() => {
    if (startedAt === null) { setDisplay("—"); return; }
    const tick = () => setDisplay(formatUptime(Date.now() / 1000 - startedAt, t));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt, t]);

  return display;
}

export function OverviewPanel() {
  const { t } = useTranslation(["dashboard", "status", "common"]);
  const setStartedAt = useAppStore((s) => s.setStartedAt);

  const { data: status } = useQuery({ queryKey: ["status"], queryFn: () => statusApi.get().then((r) => r.data), refetchInterval: 3000 });
  const { data: pfc } = useQuery({ queryKey: ["pfc"], queryFn: () => statusApi.pfc().then((r) => r.data as PfcSnapshot), refetchInterval: 3000 });
  const { data: tools } = useQuery({ queryKey: ["tools"], queryFn: () => toolsApi.list().then((r) => r.data) });
  const { data: components } = useQuery({ queryKey: ["components"], queryFn: () => statusApi.components().then((r) => r.data) });

  const isReady = status?.ready;
  const statusInfo = status?.status as Record<string, unknown> | undefined;
  const enabledTools = tools?.filter((t: { enabled: boolean }) => t.enabled).length ?? 0;
  const totalTools = tools?.length ?? 0;
  const phase = String(statusInfo?.mind_phase ?? "idle");
  const pendingTotal = (pfc?.pending_messages?.length ?? 0) + (pfc?.general_tasks?.length ?? 0);

  useEffect(() => {
    if (typeof statusInfo?.uptime === "number" && statusInfo.uptime > 0) setStartedAt(statusInfo.uptime);
  }, [statusInfo?.uptime, setStartedAt]);

  const uptimeDisplay = useUptime();

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-3">
        <StatCard label={t("runningStatus")} value={<span className="flex items-center gap-2"><StatusDot status={isReady ? "ok" : "danger"} />{isReady ? t("running", { ns: "common" }) : t("notReady", { ns: "common" })}</span>} variant={isReady ? "ok" : "danger"} />
        <StatCard label={t("thinkingPhase", { ns: "status" })} value={t(`phaseLabels.${phase}`, { ns: "status", defaultValue: phase })} variant={phase === "idle" ? "default" : "ok"} />
        <StatCard label={t("messageCount")} value={String(statusInfo?.message_count ?? "—")} />
        <StatCard label={t("tools")} value={`${enabledTools}/${totalTools}`} variant={enabledTools > 0 ? "ok" : "default"} />
        <StatCard label={t("uptime")} value={uptimeDisplay} />
        <StatCard label={t("stm", { ns: "status" })} value={`${pfc?.short_term_memory_count ?? 0}/${pfc?.short_term_memory_max ?? 0}`} />
        <StatCard label={t("pending", { ns: "status" })} value={String(pendingTotal)} variant={pendingTotal > 0 ? "warn" : "default"} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title={t("pendingTasks", { ns: "status" })}>
          {(pfc?.pending_messages?.length || pfc?.general_tasks?.length || (pfc?.pending_analysis_count ?? 0) > 0) ? (
            <div className="space-y-1.5 max-h-[260px] overflow-y-auto">
              {pfc?.pending_messages?.map((m, i) => (
                <div key={`msg-${i}`} className="py-1.5 px-3 rounded-sm bg-elevated border border-border">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-subtle text-accent font-medium">{t("message", { ns: "status" })}</span>
                    <span className="text-xs font-mono text-muted">{m.scope}</span>
                    {m.adapter_key && <span className="text-[10px] text-muted">[{m.adapter_key}]</span>}
                  </div>
                  <p className="text-xs text-foreground mt-1 truncate">{m.preview}</p>
                </div>
              ))}
              {pfc?.general_tasks?.map((task, i) => (
                <div key={`task-${i}`} className="py-1.5 px-3 rounded-sm bg-elevated border border-border">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-warn-subtle text-warn font-medium">{task.type}</span>
                    <span className="text-xs font-mono text-muted">{task.scope}</span>
                  </div>
                  <p className="text-xs text-foreground mt-1 truncate">{task.preview}</p>
                </div>
              ))}
              {(pfc?.pending_analysis_count ?? 0) > 0 && (
                <div className="py-1.5 px-3 rounded-sm bg-elevated border border-border flex items-center gap-2">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-subtle text-info font-medium">{t("analysis", { ns: "status" })}</span>
                  <span className="text-xs text-foreground">{t("entitiesWaiting", { ns: "status", count: pfc?.pending_analysis_count })}</span>
                </div>
              )}
            </div>
          ) : <p className="text-muted text-sm py-2">{t("noPendingTasks", { ns: "status" })}</p>}
        </Card>

        <Card title={t("componentInfo")} subtitle={t("componentSubtitle")}>
          {components?.lines ? (
            <div className="space-y-1 font-mono text-[13px] max-h-[260px] overflow-y-auto">
              {(components.lines as string[]).map((line: string) => (
                <div key={line} className="text-foreground py-0.5">{line}</div>
              ))}
            </div>
          ) : <p className="text-muted text-sm">{t("loading", { ns: "common" })}</p>}
        </Card>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { icon: MessageCircle, label: t("chatLink"), desc: t("chatDesc"), to: "/chat" },
          { icon: Wrench, label: t("toolsLink"), desc: t("toolsDesc"), to: "/tools" },
          { icon: Brain, label: t("memoryLink"), desc: t("memoryDesc"), to: "/memory" },
          { icon: Activity, label: t("statusLink"), desc: t("statusDesc"), to: "/thinking" },
        ].map((item) => (
          <Link key={item.label} to={item.to}
            className="group rounded-lg border border-border bg-card p-4 transition-all duration-[var(--duration-normal)] hover:border-accent hover:shadow-md hover:-translate-y-0.5 animate-[rise_0.35s_var(--ease-out)_backwards]">
            <item.icon size={20} className="text-accent mb-2" strokeWidth={1.5} />
            <div className="font-semibold text-sm text-heading">{item.label}</div>
            <div className="text-xs text-muted mt-0.5">{item.desc}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
