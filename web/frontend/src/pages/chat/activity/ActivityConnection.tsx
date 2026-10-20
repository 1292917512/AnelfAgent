import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useChatStore } from "@/stores/chat-store";
import { useActivityStore } from "@/stores/activity-store";

/** 实时传输与快照补齐独立于智能体忙闲；手动恢复复用同一同步入口。 */
export function ActivityConnection({ active }: { active: boolean }) {
  const { t } = useTranslation("workbench");
  const status = useChatStore((state) => state.streamStatus);
  const refresh = useChatStore((state) => state.refreshRealtime);
  const loading = useActivityStore((state) => state.loading);
  const label = status !== "connected" ? t(`activity.sync.${status}`) : loading ? t("activity.sync.synchronizing")
    : t(active ? "activity.working" : "activity.idle");
  return <span className="activity-connection inline-flex items-center gap-2 text-xs text-muted" role="status">
    {active && <span className="activity-live-dot" />}<span>{label}</span>
    <button type="button" aria-label={t("activity.sync.refresh")} title={t("activity.sync.refresh")} disabled={loading}
      onClick={refresh}><RefreshCw size={12} className={loading ? "animate-spin" : undefined} /></button>
  </span>;
}
