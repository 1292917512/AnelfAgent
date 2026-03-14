import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import { StatCard } from "@/components/common/StatCard";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";

type PfcSnapshot = {
  tool_recall: { name: string; count: number }[];
  tool_recall_top_n: number;
  tag_activated_tools: string[];
  pending_messages: { scope: string; preview: string; adapter_key: string }[];
  general_tasks: { type: string; scope: string; preview: string }[];
  pending_analysis_count: number;
  short_term_memory_count: number;
  short_term_memory_max: number;
  active_tools?: string[];
};

export function ToolsInsightPanel() {
  const { t } = useTranslation("status");
  const { data: pfc } = useQuery({ queryKey: ["pfc"], queryFn: () => statusApi.pfc().then((r) => r.data as PfcSnapshot), refetchInterval: 3000 });

  const toolRecall = pfc?.tool_recall ?? [];
  const maxCount = toolRecall.length > 0 ? Math.max(...toolRecall.map((tr) => tr.count)) : 1;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label={t("recalledTools")} value={String(toolRecall.length)} />
        <StatCard label={t("totalCalls")} value={String(toolRecall.reduce((s, tr) => s + tr.count, 0))} />
        <StatCard label={t("activeTools")} value={String(pfc?.active_tools?.length ?? 0)} />
        <StatCard label={t("tagActivated")} value={String(pfc?.tag_activated_tools?.length ?? 0)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title={`${t("toolCallRanking")} (Top ${pfc?.tool_recall_top_n ?? 10})`}>
          {toolRecall.length > 0 ? (
            <div className="space-y-2 max-h-[400px] overflow-y-auto">
              {toolRecall.map((tool, i) => (
                <div key={tool.name} className="group">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-mono text-[var(--text)] truncate flex items-center gap-2">
                      <span className={cn("w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0",
                        i === 0 ? "bg-[var(--accent)] text-white" : i < 3 ? "bg-[var(--accent-subtle)] text-[var(--accent)]" : "bg-[var(--secondary)] text-[var(--muted)]"
                      )}>{i + 1}</span>
                      {tool.name}
                    </span>
                    <span className="text-xs font-semibold text-[var(--accent)] ml-2 flex-shrink-0">{t("times", { count: tool.count })}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-[var(--secondary)] overflow-hidden">
                    <div className="h-full rounded-full bg-[var(--accent)] transition-all duration-500" style={{ width: `${(tool.count / maxCount) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          ) : <p className="text-[var(--muted)] text-sm py-4 text-center">{t("noToolCalls")}</p>}
        </Card>

        <Card title={`${t("currentActiveTools")} (${pfc?.active_tools?.length ?? 0})`}>
          {pfc?.active_tools?.length ? (
            <div className="flex flex-wrap gap-1.5">
              {pfc.active_tools.map((name) => {
                const recall = toolRecall.find((tr) => tr.name === name);
                return (
                  <span key={name} className={cn("px-2.5 py-1 text-[11px] font-mono rounded-[var(--radius-md)] border transition-all",
                    recall ? "bg-[var(--accent-subtle)] text-[var(--accent)] border-[var(--accent)]" : "bg-[var(--bg-elevated)] text-[var(--text)] border-[var(--border)]"
                  )}>{name}{recall ? ` (${recall.count})` : ""}</span>
                );
              })}
            </div>
          ) : <p className="text-[var(--muted)] text-sm py-4 text-center">{t("noActiveTools")}</p>}
        </Card>

        <Card title={t("tagActivatedTools")}>
          {pfc?.tag_activated_tools?.length ? (
            <div className="flex flex-wrap gap-2">
              {pfc.tag_activated_tools.map((name) => (
                <span key={name} className="px-2.5 py-1 text-xs font-mono rounded-full bg-[var(--accent-subtle)] text-[var(--accent)] border border-[var(--accent)]">{name}</span>
              ))}
            </div>
          ) : <p className="text-[var(--muted)] text-sm py-2">{t("noTagTools")}</p>}
        </Card>
      </div>
    </div>
  );
}
