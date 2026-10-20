import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { hasActiveWork, activeChatRun } from "@/lib/chat-activity";
import { runtimeStatusQuery, runtimeThinkingQuery } from "@/lib/runtime-queries";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import { useShallow } from "zustand/react/shallow";

/** 补充尚未产生轮次的阶段和队列事实；静默时复用活动快照恢复，不合成执行记录。 */
export function ActivityRuntime() {
  const { t } = useTranslation("workbench");
  const waitingIds = useChatStore(useShallow((state) => Object.entries(state.buckets).filter(([, bucket]) => bucket.sending).map(([id]) => id)));
  const waiting = waitingIds.length;
  const { observed, loaded, unobserved } = useActivityStore(useShallow((state) => ({
    observed: state.runs.some(hasActiveWork), loaded: state.loaded,
    unobserved: waitingIds.some((id) => !activeChatRun(state.runs, id)),
  })));
  const status = useQuery({ ...runtimeStatusQuery(), refetchInterval: observed && !waiting ? 15_000 : 5000, refetchOnWindowFocus: true });
  const thinking = useQuery({ ...runtimeThinkingQuery(), enabled: !observed || waiting > 0, refetchInterval: 5000, refetchOnWindowFocus: true });
  const phase = !status.isError ? status.data?.status?.mind_phase : undefined;
  const busy = !!phase && !["idle", "unknown"].includes(phase);
  const pending = !thinking.isError ? thinking.data?.pending_messages ?? [] : [];
  const tasks = !thinking.isError ? thinking.data?.general_tasks?.length ?? 0 : 0;
  const queued = !status.isError ? status.data?.status?.queue_size ?? 0 : 0;

  useEffect(() => {
    const activity = useActivityStore.getState();
    if (!document.hidden && !waiting && (busy || observed || pending.length || tasks || queued)
      && Date.now() - activity.syncedAt >= 10_000) void activity.load();
  }, [status.dataUpdatedAt, thinking.dataUpdatedAt, busy, observed, waiting, pending.length, tasks, queued]);

  if (observed && !unobserved) return null;
  const unavailable = status.isError || thinking.isError;
  const checking = !loaded || status.isPending || thinking.isPending;
  if (!waiting && phase === "idle" && !pending.length && !tasks && !queued && !unavailable && !checking && status.data?.ready) return null;
  const label = unavailable ? t("activity.runtime.unavailable") : checking ? t("activity.runtime.checking")
    : !status.data?.ready ? t("activity.runtime.notReady") : busy ? t("activity.runtime.phase", { phase: t(`status:phaseLabels.${phase}`, { defaultValue: phase }) })
      : waiting || pending.length || tasks || queued ? t("activity.runtime.waiting") : t("activity.runtime.unknown");
  return <aside className="mx-4 my-3 min-w-0 shrink-0 rounded-xl border border-border bg-elevated px-4 py-3 text-xs" aria-label={t("activity.runtime.title")}>
    <p className="font-medium" role="status">{label}</p>
    {waiting > 0 && <p className="mt-2 text-muted">{t("activity.runtime.submitted", { count: waiting })}</p>}
    {(pending.length > 0 || tasks > 0 || queued > 0) && <p className="mt-2 text-muted">{t("activity.runtime.queue", { messages: pending.length, tasks, incoming: queued })}</p>}
    {!!pending.length && <ul className="mt-2 space-y-1 text-muted">{pending.slice(0, 5).map((item) => <li key={item.scope} className="truncate" title={`${item.scope}\n${item.preview}`}>
      <span className="mr-2 font-medium">{item.adapter_key || t("activity.system")}</span>{item.preview}
    </li>)}</ul>}
    {!observed && (busy || waiting > 0) && <p className="mt-2 text-muted">{t("activity.runtime.noRecord")}</p>}
  </aside>;
}
