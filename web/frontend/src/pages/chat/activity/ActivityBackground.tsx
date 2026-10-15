import { ArrowUpRight, Check, CircleAlert, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNow } from "@/hooks/useNow";
import { formatElapsedCompact } from "@/lib/format";
import type { ActivityEntry } from "@/lib/types/activity";
import { cn } from "@/lib/utils";
import { ActivityInspector } from "./ActivityInspector";
import { ActivityToolResult } from "./ActivityToolResult";

/** 后台任务只展示状态和耗时；结果在打开详情后渲染。 */
export function ActivityBackground({ entry }: { entry: Extract<ActivityEntry, { kind: "background" }> }) {
  const { t } = useTranslation("workbench");
  const running = entry.status === "running";
  const now = useNow(running);
  const duration = formatElapsedCompact(Math.max(0, running ? now - entry.ts * 1000 : entry.duration_ms ?? 0));
  const title = t("activity.side.background");
  const StatusIcon = running ? Loader2 : entry.status === "done" ? Check : CircleAlert;
  return <div className={cn("activity-tool", entry.status === "error" && "is-error")}>
    <ActivityInspector title={title} preview={entry.description} className="activity-tool-trigger" trigger={<>
      <StatusIcon size={15} className={cn("shrink-0", running ? "animate-spin text-accent" : entry.status === "done" ? "text-ok" : "text-warn")} />
      <span className="min-w-0 flex-1 truncate">{entry.description}</span>
      <span className="shrink-0 text-xs tabular-nums text-muted">{t(`activity.status.${entry.status}`)} · {duration}</span>
      <ArrowUpRight size={14} className="shrink-0 text-muted" />
    </>}>
      <div className="activity-inspection-meta"><span>{t(`activity.status.${entry.status}`)}</span><span>{duration}</span></div>
      <p className="break-words">{entry.description}</p>
      {entry.result && <ActivityToolResult result={entry.result} />}
      <p className="activity-trace-id">{entry.task_kind} · {entry.id}</p>
    </ActivityInspector>
  </div>;
}
