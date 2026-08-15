/**
 * DelegationCard — 消息流中的子代理执行卡片。
 *
 * 渲染 DelegationNode 的实时状态：goal / role / 状态徽标 / 实时进度
 * （当前轮次 / 正在使用的工具）/ 完成输出 / 错误信息。
 * 运行中可手动取消（取消后由后端 delegation_resolved 事件确认终态）。
 */
import { useTranslation } from "react-i18next";
import { Bot, CheckCircle2, ChevronDown, ChevronRight, CircleSlash, Loader2, XCircle, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { chatApi } from "@/lib/api";
import { useDelegationStore } from "@/stores/delegation-store";
import type { DelegationNode } from "@/lib/types";

interface Props {
  node: DelegationNode;
}

export function DelegationCard({ node }: Props) {
  const { t } = useTranslation("plan");
  const [expanded, setExpanded] = useState(false);
  const markCancelling = useDelegationStore((s) => s.markCancelling);

  const running = node.status === "running";
  // 运行中每秒钟刷新耗时（避免渲染期直接调用 Date.now）
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const durationSec = Math.round((node.resolved_at ?? now) - node.started_at);

  const handleCancel = async () => {
    markCancelling(node.chat_id, node.delegation_id);
    try {
      await chatApi.cancelDelegation(node.delegation_id);
    } catch { /* 取消失败时由 delegation_resolved 或超时兜底 */ }
  };

  return (
    <div className="flex justify-start">
      <div
        className={cn(
          "max-w-[88%] sm:max-w-[80%] w-full rounded-lg border overflow-hidden text-sm",
          node.status === "failed"
            ? "border-red-400/40 bg-red-50/40 dark:bg-red-950/20"
            : node.status === "completed"
              ? "border-green-400/40 bg-green-50/20 dark:bg-green-950/10"
              : node.status === "cancelled"
                ? "border-border/60 bg-muted/30"
                : "border-blue-400/40 bg-blue-50/30 dark:bg-blue-950/20",
        )}
      >
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-hover/50 transition-colors"
        >
          <Bot size={14} className={cn("shrink-0",
            node.status === "failed" ? "text-red-500"
              : node.status === "completed" ? "text-green-500"
                : node.status === "cancelled" ? "text-muted"
                  : "text-blue-500",
          )} />
          <span className={cn(
            "text-sm font-medium flex-1 min-w-0 truncate",
            node.status === "cancelled" ? "text-muted" : "text-foreground",
          )}>
            {node.goal || t("delegation.untitled")}
          </span>
          <span className="text-xs text-muted shrink-0 font-mono">
            {durationSec}s
          </span>
          {node.background && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300 shrink-0">
              {t("delegation.background")}
            </span>
          )}
          {node.role === "orchestrator" && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300 shrink-0">
              <Zap size={9} className="inline -mt-0.5 mr-0.5" />
              {t("delegation.orchestrator", { defaultValue: "orchestrator" })}
            </span>
          )}
          {expanded ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
        </button>

        {/* 运行中：实时进度行 + 取消按钮（常显，无需展开） */}
        {running && (
          <div className="flex items-center gap-2 px-3 py-1.5 border-t border-border/50 text-[11px] text-muted">
            <Loader2 size={11} className="animate-spin shrink-0 text-blue-500" />
            <span className="flex-1 min-w-0 truncate">
              {node.cancelling
                ? t("delegation.cancelling")
                : node.current_tool
                  ? t("delegation.progress.usingTool", { tool: node.current_tool })
                  : node.iteration
                    ? t("delegation.progress.round", { n: node.iteration })
                    : t("delegation.running")}
            </span>
            {!node.cancelling && (
              <button
                onClick={handleCancel}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-danger hover:bg-danger-subtle transition-colors shrink-0"
              >
                <CircleSlash size={11} />
                {t("delegation.cancel")}
              </button>
            )}
          </div>
        )}

        {expanded && (
          <div className="px-3 pb-2 space-y-1.5 border-t border-border/50">
            {node.context_preview && (
              <div className="text-xs text-muted mt-1.5">
                <span className="font-medium mr-1">{t("delegation.context")}:</span>
                {node.context_preview}
              </div>
            )}
            {node.status === "completed" && node.output && (
              <div className="text-xs text-foreground/80 mt-1.5 max-h-32 overflow-y-auto whitespace-pre-wrap break-words">
                <span className="font-medium mr-1 text-green-600 dark:text-green-400">
                  {t("delegation.output")}:
                </span>
                {node.output}
              </div>
            )}
            {node.status === "failed" && node.error && (
              <div className="text-xs text-red-600 dark:text-red-400 mt-1.5 max-h-32 overflow-y-auto whitespace-pre-wrap break-words">
                <span className="font-medium mr-1">{t("delegation.error")}:</span>
                {node.error}
              </div>
            )}
            <div className="flex items-center gap-2 pt-1.5 border-t border-border/50 text-[11px]">
              {node.status === "completed" && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-300">
                  <CheckCircle2 size={10} />
                  {t("delegation.completed")}
                </span>
              )}
              {node.status === "failed" && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-300">
                  <XCircle size={10} />
                  {t("delegation.failed")}
                </span>
              )}
              {node.status === "cancelled" && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-muted text-muted">
                  <CircleSlash size={10} />
                  {t("delegation.cancelled")}
                </span>
              )}
              {node.status === "running" && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300">
                  <Loader2 size={10} className="animate-spin" />
                  {t("delegation.running")}
                </span>
              )}
              <span className="text-muted">
                {t("delegation.role")}: {node.role}
              </span>
              {node.depth > 0 && (
                <span className="text-muted">
                  {t("delegation.depth")}: {node.depth}
                </span>
              )}
              {node.agent && (
                <span className="text-accent truncate" title={node.agent}>
                  @{node.agent}
                </span>
              )}
              {node.model && (
                <span className="text-muted truncate" title={node.model}>
                  {t("delegation.model")}: {node.model}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
