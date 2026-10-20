import { useState } from "react";
import { Play, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui";
import { workspaceApi } from "@/lib/api/workspace";
import { apiErrorMessage } from "@/lib/api/client";
import type { ActivityRun } from "@/lib/types";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import { toast } from "@/stores/toast-store";

/** 可停止能力来自运行时，续接沿用最近 Web 会话；终态依赖服务端确认。 */
export function ActivityExecutionControl({ run }: { run: ActivityRun }) {
  const { t } = useTranslation("workbench");
  const latest = useActivityStore((state) => state.runs.filter((item) => item.scope === run.scope && item.kind === "conversation").slice(-1)[0]?.id);
  const [pending, setPending] = useState(false);
  const web = run.kind === "conversation" && run.source.channel === "webui" && run.source.target === "web_user" && latest === run.id;
  if (run.status === "running" ? !run.can_stop : !web) return null;
  const stopping = run.status === "running" && run.control === "stopping";
  const resuming = run.control === "resuming" || run.control === "resumed";
  if (run.status !== "running" && run.status !== "cancelled") return null;
  const action = run.status === "running" ? "stop" : "resume";
  const label = t(`activity.control.${stopping ? "stopping" : resuming ? "resuming" : action}`);
  const control = async () => {
    setPending(true);
    try {
      const { data } = await workspaceApi.controlActivity(run.id, action);
      if (data.status === "error") throw new Error(data.error || t("activity.control.failed"));
      const failed = data.background?.filter((item) => !item.ok) ?? [];
      if (failed.length) toast.error(t("activity.control.backgroundPending", { count: failed.length }));
      if (action === "resume") void useChatStore.getState().refreshAfterReconnect(run.source.session || "default");
    } catch (error) {
      toast.error(apiErrorMessage(error, t("activity.control.failed")));
    } finally {
      await useActivityStore.getState().load();
      setPending(false);
    }
  };
  return <Button size="sm" variant={action === "stop" ? "ghost" : "secondary"}
    className={action === "stop" ? "text-warn" : undefined} disabled={stopping || resuming}
    loading={pending} aria-label={label} title={t(`activity.control.${action}Hint`)} onClick={() => void control()}>
    {action === "stop" ? <Square size={13} /> : <Play size={13} />}<span className="activity-control-label">{label}</span>
  </Button>;
}
