/**
 * 状态胶囊 — 对话区右上角浮动的工作状态指示（ZCode ConversationStatusPanel 形态）。
 *
 * 优先级链（只显示最重要一件事）：
 *   1. 思维链开启且有活跃会话：当前节点（运行中优先）+ 错误计数 + 耗时，点击进思维面板
 *   2. 对话正在工作（发送中流式）：「工作中 X」计时，无点击目标
 *   3. 空闲：不渲染
 * 外层 pointer-events-none 不挡消息流，胶囊本身可点。
 */

import { useTranslation } from "react-i18next";
import { Activity, AlertCircle, Brain, MessageSquare, Wrench, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { useThinkingStore } from "@/stores/thinking-store";
import type { TraceNode } from "@/lib/types";
import { sessionChatId } from "@/components/thinking/trace-plans";
import { useChatStore } from "@/stores/chat-store";
import { useNow } from "@/hooks/useNow";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useThinkingBootstrap } from "@/hooks/useThinking";

const TYPE_ICONS: Record<string, typeof Activity> = {
  llm_call: Brain,
  tool_call: Wrench,
  decision: Zap,
  reply_round: MessageSquare,
  situation: Activity,
  phase_change: Activity,
  context_build: Activity,
};

function formatElapsed(startTsSec: number): string {
  const sec = Math.max(0, Date.now() / 1000 - startTsSec);
  if (sec < 60) return `${sec.toFixed(0)}s`;
  return `${Math.floor(sec / 60)}m${String(Math.floor(sec % 60)).padStart(2, "0")}s`;
}

/** 思维链会话态：当前节点 + 错误计数 + 耗时（点击进思维面板） */
function TraceCapsule() {
  const { t } = useTranslation("workbench");
  const connected = useThinkingStore((s) => s.connected);
  const activeSession = useThinkingStore((s) => s.activeSession);
  const setActiveTab = useWorkbenchStore((s) => s.setActiveTab);

  const chatId = useChatStore((state) => state.activeChatId);
  const hasRunning = activeSession?.nodes.some((n) => n.status === "running") ?? false;
  useNow(hasRunning);

  if (!activeSession || activeSession.ended || sessionChatId(activeSession) !== chatId) return <WorkingCapsule />;

  const nodes = activeSession.nodes;
  const runningNode = [...nodes].reverse().find((n) => n.status === "running");
  const errorCount = nodes.filter((n) => n.status === "error").length;
  const lastNode: TraceNode | undefined = runningNode ?? nodes[nodes.length - 1];
  if (!lastNode) return null;

  const Icon = TYPE_ICONS[lastNode.type] ?? Activity;
  const hasError = errorCount > 0;

  return (
    <button
      onClick={() => setActiveTab("trace")}
      className={cn(
        "pointer-events-auto flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] shadow-sm backdrop-blur transition-colors max-w-64",
        hasError
          ? "border-[rgba(239,68,68,0.4)] bg-danger-subtle text-danger"
          : "border-border bg-elevated/90 text-muted hover:text-foreground",
      )}
    >
      <span
        className={cn(
          "inline-block w-1.5 h-1.5 rounded-full shrink-0",
          hasError ? "bg-danger" : connected ? "bg-ok animate-pulse" : "bg-muted",
        )}
      />
      <Icon size={12} className="shrink-0" />
      <span className="truncate">{lastNode.label}</span>
      {hasError && (
        <span className="flex items-center gap-0.5 shrink-0">
          <AlertCircle size={11} />
          {t("statusbar.errors", { count: errorCount })}
        </span>
      )}
      {runningNode && (
        <span className="shrink-0 font-mono tabular-nums">{formatElapsed(runningNode.timestamp)}</span>
      )}
    </button>
  );
}

/** 对话工作态：发送中的「工作中 X」计时（思维链未开启时的兜底感知） */
function WorkingCapsule() {
  const { t } = useTranslation("workbench");
  const activeChatId = useChatStore((s) => s.activeChatId);
  const sendingSince = useChatStore((s) => s.buckets[activeChatId]?.sendingSince ?? null);
  useNow(sendingSince !== null);

  if (sendingSince === null) return null;
  return (
    <div className="pointer-events-auto flex items-center gap-1.5 rounded-full border border-border bg-elevated/90 backdrop-blur px-2.5 py-1 text-[11px] text-muted shadow-sm">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-ok animate-pulse shrink-0" />
      <Activity size={12} className="shrink-0" />
      <span className="shrink-0">
        {t("statusbar.working")}
        <span className="font-mono tabular-nums">{formatElapsed(sendingSince / 1000)}</span>
      </span>
    </div>
  );
}

/** 当前对话工作状态，空闲时不占空间。 */
export function StatusCapsule() {
  useThinkingBootstrap();
  const enabled = useThinkingStore((s) => s.enabled);

  return (
    <div className="pointer-events-none flex justify-end empty:hidden">
      {enabled ? <TraceCapsule /> : <WorkingCapsule />}
    </div>
  );
}
