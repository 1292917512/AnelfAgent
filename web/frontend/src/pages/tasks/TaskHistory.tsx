import { useQuery } from "@tanstack/react-query";
import { tasksApi } from "@/lib/api";
import type { TaskConfig } from "@/lib/types";
import { cn, formatAge, formatDurationMs } from "@/lib/utils";
/** 本文件辅助组件共用的 i18n t 签名（支持插值参数） */
type Tf = (key: string, options?: Record<string, unknown>) => string;

/** 执行状态 → 展示文案与配色（success/no_output/error 三态） */
function runStatusMeta(status: string, t: Tf) {
  switch (status) {
    case "success":
      return { text: t("tasks.runStatusSuccess"), cls: "text-ok" };
    case "no_output":
      return { text: t("tasks.runStatusNoOutput"), cls: "text-muted" };
    case "error":
      return { text: t("tasks.runStatusError"), cls: "text-danger" };
    default:
      return { text: status, cls: "text-muted" };
  }
}

/** 触发来源 → 展示文案（未收录词原样返回） */
function runTriggerLabel(trigger: string, t: Tf) {
  const key = `tasks.trigger_${trigger}`;
  const label = t(key);
  return label === key ? trigger : label;
}

/** 任务行内的最近一次执行概况（无记录返回 null） */
export function LastRunLine({ lastRun, t }: {
  lastRun: NonNullable<TaskConfig["last_run"]>;
  t: Tf;
}) {
  const status = runStatusMeta(lastRun.status, t);
  return (
    <p className="text-[11px] text-muted mt-0.5">
      {t("tasks.lastRunPrefix")}
      <span className="tabular-nums">{formatAge(lastRun.started_at)}</span>
      {t("tasks.lastRunAgoSuffix")}
      {" · "}
      <span className={cn("font-medium", status.cls)}>{status.text}</span>
      {" · "}
      {t("tasks.lastRunDuration", { duration: formatDurationMs(lastRun.duration_ms) })}
      {" · "}
      {runTriggerLabel(lastRun.trigger, t)}
    </p>
  );
}

/** 展开区的执行记录列表（挂载时拉取，仅在展开且非编辑态渲染） */
export function TaskHistoryList({ name, t }: { name: string; t: Tf }) {
  const { data: records = [], isLoading } = useQuery({
    queryKey: ["task-history", name],
    queryFn: () => tasksApi.history(name).then((r) => r.data),
  });

  return (
    <div className="pt-2 mt-2 border-t border-border">
      <p className="font-medium mb-1.5">{t("tasks.historyTitle")}</p>
      {isLoading ? (
        <p className="text-muted">{t("tasks.historyLoading")}</p>
      ) : records.length === 0 ? (
        <p className="text-muted">{t("tasks.historyEmpty")}</p>
      ) : (
        <div className="space-y-1.5">
          {records.map((rec, i) => {
            const status = runStatusMeta(rec.status, t);
            return (
              <div key={`${rec.started_at}-${i}`} className="text-[11px] bg-elevated border border-border rounded px-2 py-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="tabular-nums text-muted">
                    {new Date(rec.started_at * 1000).toLocaleString(undefined, {
                      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                    })}
                  </span>
                  <span className={cn("font-medium", status.cls)}>{status.text}</span>
                  <span className="text-muted tabular-nums">{formatDurationMs(rec.duration_ms)}</span>
                  <span className="text-muted">{runTriggerLabel(rec.trigger, t)}</span>
                </div>
                {rec.error && <p className="text-danger mt-1 break-all">{rec.error}</p>}
                {rec.preview && <p className="text-foreground/80 mt-1 line-clamp-2 break-all">{rec.preview}</p>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
