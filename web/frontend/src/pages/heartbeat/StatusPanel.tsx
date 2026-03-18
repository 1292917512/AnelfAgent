import { useQuery, useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { heartbeatApi, statusApi, memoryApi, tasksApi, type TaskConfig } from "@/lib/api";
import { RefreshCw, Play, Zap } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

export function StatusPanel() {
  const { t } = useTranslation("heartbeat");
  const [logRefreshKey, setLogRefreshKey] = useState(0);

  const { data: agentStatus } = useQuery({
    queryKey: ["agentStatus"],
    queryFn: () => statusApi.get().then((r) => r.data),
    refetchInterval: 5000,
  });

  const { data: hbStatus } = useQuery({
    queryKey: ["heartbeat-status"],
    queryFn: () => heartbeatApi.getStatus().then((r) => r.data),
    refetchInterval: 5000,
  });

  const { data: tasks = [] } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => tasksApi.list().then((r) => r.data as TaskConfig[]),
  });

  const { data: heartbeatLog, isLoading: logLoading } = useQuery({
    queryKey: ["heartbeatLog", logRefreshKey],
    queryFn: () =>
      memoryApi.files.read("config/memory/heartbeat.md").then((r) => r.data?.content ?? r.data ?? ""),
    refetchInterval: 30000,
  });

  const triggerMut = useMutation({ mutationFn: () => heartbeatApi.trigger() });
  const triggerTaskMut = useMutation({ mutationFn: (name: string) => tasksApi.trigger(name) });

  const isRunning = !!agentStatus && agentStatus.status !== "stopped";
  const interval = hbStatus?.interval_seconds ?? 300;
  const totalTicks = hbStatus?.total_ticks ?? 0;

  const formatTime = (seconds: number) => {
    if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${seconds}s`;
  };

  const schedules = hbStatus?.schedules ?? [];
  const taskMap = new Map((tasks as TaskConfig[]).map((t) => [t.name, t]));

  const logLines: string[] =
    typeof heartbeatLog === "string" && heartbeatLog.trim()
      ? heartbeatLog.split("\n### ").filter((b: string) => b.trim()).slice(-6).reverse()
      : [];

  return (
    <div className="space-y-4">
      {/* 顶部状态指标 */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <div className="flex items-center gap-3 p-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)]">
          <StatusDot status={isRunning && hbStatus?.enabled ? "ok" : "offline"} />
          <div>
            <p className="text-[10px] text-[var(--muted)] uppercase tracking-wide">{t("status.title")}</p>
            <p className="text-sm font-semibold text-[var(--text-strong)]">
              {!isRunning ? t("status.stopped") : hbStatus?.enabled ? t("status.running") : t("status.paused")}
            </p>
          </div>
        </div>
        <div className="p-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)]">
          <p className="text-[10px] text-[var(--muted)] uppercase tracking-wide">{t("status.interval")}</p>
          <p className="text-sm font-semibold text-[var(--text-strong)]">{formatTime(interval)}</p>
        </div>
        <div className="p-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)]">
          <p className="text-[10px] text-[var(--muted)] uppercase tracking-wide">{t("status.totalTicks")}</p>
          <p className="text-sm font-semibold text-[var(--text-strong)]">{totalTicks}</p>
        </div>
        <div className="p-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)]">
          <p className="text-[10px] text-[var(--muted)] uppercase tracking-wide">{t("status.scheduledTasks")}</p>
          <p className="text-sm font-semibold text-[var(--text-strong)]">{schedules.length}</p>
        </div>
        {agentStatus?.uptime != null && (
          <div className="p-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)]">
            <p className="text-[10px] text-[var(--muted)] uppercase tracking-wide">{t("status.uptime")}</p>
            <p className="text-sm font-semibold text-[var(--text-strong)]">{formatTime(agentStatus.uptime)}</p>
          </div>
        )}
      </div>

      {/* 手动心跳按钮 */}
      <div className="flex gap-2">
        <button
          onClick={() => triggerMut.mutate()}
          disabled={triggerMut.isPending || !isRunning}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-50 transition-all"
        >
          <Zap size={13} /> {triggerMut.isPending ? t("status.triggering") : t("status.triggerHeartbeat")}
        </button>
      </div>

      {/* 任务调度进度 */}
      {schedules.length > 0 && (
        <Card title={t("status.taskProgress")} subtitle={t("status.taskProgressDesc")}>
          <div className="space-y-2">
            {schedules.map((s) => {
              const task = taskMap.get(s.task_name);
              const displayName = task?.display_name || s.task_name;
              const isDisabled = !s.task_enabled || !s.task_exists;

              return (
                <div
                  key={s.task_name}
                  className={cn(
                    "flex items-center gap-3 p-2.5 rounded-[var(--radius-md)] border border-[var(--border)]",
                    isDisabled ? "opacity-50 bg-[var(--bg-base)]" : "bg-[var(--bg-elevated)]",
                  )}
                >
                  <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", isDisabled ? "bg-[var(--muted)]" : "bg-[var(--ok)]")} />

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-strong)] truncate">{displayName}</span>
                      <span className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded-full",
                        s.mode === "heartbeat" ? "bg-blue-500/10 text-blue-500" :
                        s.mode === "scheduled" ? "bg-amber-500/10 text-amber-500" :
                        "bg-gray-500/10 text-gray-400",
                      )}>
                        {t(`schedule.mode${s.mode.charAt(0).toUpperCase()}${s.mode.slice(1)}`)}
                      </span>
                    </div>

                    {s.mode === "heartbeat" && (
                      <div className="flex items-center gap-2 mt-1">
                        <div className="flex-1 h-1.5 rounded-full bg-[var(--border)] overflow-hidden max-w-[200px]">
                          <div
                            className="h-full rounded-full bg-[var(--accent)] transition-all"
                            style={{ width: `${Math.min(100, ((s.beat_count ?? 0) / (s.every_n_beats ?? 10)) * 100)}%` }}
                          />
                        </div>
                        <span className="text-[11px] text-[var(--muted)] tabular-nums">
                          {s.beat_count ?? 0}/{s.every_n_beats ?? 10}
                        </span>
                        <span className="text-[11px] text-[var(--muted)]">
                          ({t("status.remaining", { count: Math.max(0, (s.every_n_beats ?? 10) - (s.beat_count ?? 0)) })} ≈ {formatTime(Math.max(0, (s.every_n_beats ?? 10) - (s.beat_count ?? 0)) * interval)})
                        </span>
                      </div>
                    )}

                    {s.mode === "scheduled" && (
                      <p className="text-[11px] text-[var(--muted)] mt-0.5">
                        {t("schedule.times")}: {(s.schedule_times ?? []).join(", ") || "—"}
                      </p>
                    )}
                  </div>

                  <button
                    onClick={() => triggerTaskMut.mutate(s.task_name)}
                    disabled={triggerTaskMut.isPending || isDisabled}
                    className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] disabled:opacity-40 transition-colors"
                    title={t("status.runNow")}
                  >
                    <Play size={11} /> {t("status.runNow")}
                  </button>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* 心跳日志 */}
      <Card
        title={t("status.recentLog")}
        actions={
          <button
            onClick={() => setLogRefreshKey((k) => k + 1)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
          >
            <RefreshCw size={14} /> {t("status.refreshLog")}
          </button>
        }
      >
        {logLoading ? (
          <p className="text-sm text-[var(--muted)]">{t("status.loadingLog")}</p>
        ) : logLines.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">{t("status.noLog")}</p>
        ) : (
          <div className="space-y-2 max-h-[360px] overflow-y-auto">
            {logLines.map((block, i) => {
              const lines = block.split("\n").filter((l) => l.trim());
              const header = lines[0]?.replace(/^#+\s*/, "").trim() ?? "";
              const body = lines.slice(1);
              return (
                <div key={i} className="p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
                  <p className="text-xs font-semibold text-[var(--accent)] mb-0.5">{header}</p>
                  {body.map((line, j) => (
                    <p key={j} className="text-[11px] text-[var(--text)] font-mono leading-relaxed">{line}</p>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
