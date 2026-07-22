import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ExternalLink, HeartPulse, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { heartbeatApi } from "@/lib/api";
import { useThinkingStore } from "@/stores/thinking-store";
import { useChatStore } from "@/stores/chat-store";
import { useThinkingBootstrap } from "../useThinkingBootstrap";
import { Button } from "@/components/ui";

/** 实时状态面板：活跃会话概要 + 错误高亮 + 快捷调试操作 */
export function StatusPanel() {
  const { t } = useTranslation("workbench");
  useThinkingBootstrap();
  const queryClient = useQueryClient();

  const connected = useThinkingStore((s) => s.connected);
  const enabled = useThinkingStore((s) => s.enabled);
  const activeSession = useThinkingStore((s) => s.activeSession);
  const clearMessages = useChatStore((s) => s.clearMessages);

  const heartbeatTrigger = useMutation({
    mutationFn: () => heartbeatApi.trigger(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["heartbeatStatus"] }),
  });

  const nodes = activeSession?.nodes ?? [];
  const errorNodes = nodes.filter((n) => n.status === "error");
  const toolCalls = nodes.filter((n) => n.type === "tool_call");
  const failedTools = toolCalls.filter((n) => n.status === "error");

  return (
    <div className="p-3 space-y-4">
      {/* 连接状态 */}
      <div className="flex items-center gap-2 text-xs text-muted">
        <span className={`inline-block w-1.5 h-1.5 rounded-full ${connected ? "bg-ok animate-pulse" : "bg-muted"}`} />
        {enabled
          ? connected ? t("status.sseConnected") : t("status.sseDisconnected")
          : t("status.tracingDisabled")}
      </div>

      {/* 活跃会话概要 */}
      {activeSession ? (
        <section className="space-y-2">
          <h4 className="text-xs font-semibold text-heading">{t("status.activeSession")}</h4>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-md bg-elevated border border-border py-2">
              <div className="text-base font-semibold text-heading">{nodes.length}</div>
              <div className="text-[10px] text-muted">{t("status.nodes")}</div>
            </div>
            <div className="rounded-md bg-elevated border border-border py-2">
              <div className="text-base font-semibold text-heading">{toolCalls.length}</div>
              <div className="text-[10px] text-muted">{t("status.toolCalls")}</div>
            </div>
            <div className={`rounded-md border py-2 ${errorNodes.length ? "bg-danger-subtle border-[rgba(239,68,68,0.4)]" : "bg-elevated border-border"}`}>
              <div className={`text-base font-semibold ${errorNodes.length ? "text-danger" : "text-heading"}`}>{errorNodes.length}</div>
              <div className="text-[10px] text-muted">{t("status.errors")}</div>
            </div>
          </div>
        </section>
      ) : (
        <p className="text-xs text-muted">{t("status.noSession")}</p>
      )}

      {/* 错误与失败工具（暴露问题） */}
      {errorNodes.length > 0 && (
        <section className="space-y-1.5">
          <h4 className="flex items-center gap-1 text-xs font-semibold text-danger">
            <AlertCircle size={12} /> {t("status.problems")}
          </h4>
          <div className="space-y-1">
            {errorNodes.slice(-5).map((n) => (
              <div key={n.id} className="rounded-md border border-[rgba(239,68,68,0.3)] bg-danger-subtle px-2 py-1.5">
                <div className="text-[11px] font-medium text-danger truncate">{n.label}</div>
                {typeof n.data?.error === "string" && (
                  <div className="text-[10px] text-danger/80 break-all line-clamp-2">{n.data.error}</div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
      {failedTools.length === 0 && errorNodes.length === 0 && activeSession && (
        <p className="text-[11px] text-ok">{t("status.healthy")}</p>
      )}

      {/* 快捷调试操作 */}
      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-heading">{t("status.quickActions")}</h4>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => heartbeatTrigger.mutate()}
            disabled={heartbeatTrigger.isPending}
          >
            <HeartPulse size={13} /> {t("status.triggerHeartbeat")}
          </Button>
          <Button variant="secondary" size="sm" onClick={clearMessages}>
            <Trash2 size={13} /> {t("status.clearChat")}
          </Button>
          <Link to="/thinking">
            <Button variant="ghost" size="sm">
              <ExternalLink size={13} /> {t("status.fullThinking")}
            </Button>
          </Link>
        </div>
        {heartbeatTrigger.isSuccess && (
          <p className="text-[11px] text-ok">{t("status.heartbeatTriggered")}</p>
        )}
        {heartbeatTrigger.isError && (
          <p className="text-[11px] text-danger">{t("status.heartbeatFailed")}</p>
        )}
      </section>
    </div>
  );
}
