import { Check, CircleAlert, Loader2, Pause } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityEntry } from "@/lib/types/activity";
import { useNow } from "@/hooks/useNow";
import { formatElapsedCompact } from "@/lib/format";
import { ActivityInspector } from "./ActivityInspector";

/** 主流程只呈现阶段事实，完整准备证据仍由所属详情承载。 */
export function ActivityPhase({ entry }: { entry: Extract<ActivityEntry, { kind: "phase" | "hook" }> }) {
  const { t } = useTranslation("workbench");
  const running = entry.status === "running";
  const now = useNow(running);
  const title = entry.kind === "hook" ? t(`settings:hookFlow.names.${entry.name}`, { defaultValue: entry.name })
    : t(`activity.phases.${entry.name}`, { defaultValue: entry.name });
  const failed = entry.status === "error" || entry.status === "blocked";
  const detail = entry.kind === "phase" ? entry.error || entry.detail : entry.operation || entry.detail;
  return <div className="activity-phase">
    <ActivityInspector title={title} preview={detail || title} className="activity-tool-trigger" trigger={<>
      {running ? <Loader2 size={14} className="animate-spin text-accent" /> : failed ? <CircleAlert size={14} className="text-warn" /> : entry.status === "done" ? <Check size={14} className="text-muted" /> : <Pause size={14} className="text-muted" />}
      <span className="min-w-0 flex-1 truncate">{title}</span>
      <span className="text-xs text-muted">{t(`activity.status.${entry.status}`, { defaultValue: entry.status })} · {formatElapsedCompact(Math.max(0, running ? now - entry.ts * 1000 : entry.duration_ms))}</span>
    </>}><p className="activity-note">{detail || title}</p></ActivityInspector>
    {detail && <p className="activity-note px-3 pb-2">{detail}</p>}
  </div>;
}
