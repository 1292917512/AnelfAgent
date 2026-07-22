import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Activity, AlertCircle, ChevronRight, Wrench, Brain, MessageSquare, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { useThinkingStore, type TraceNode } from "@/stores/thinking-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useThinkingBootstrap } from "./useThinkingBootstrap";

const TYPE_ICONS: Record<string, typeof Activity> = {
  llm_call: Brain,
  tool_call: Wrench,
  decision: Zap,
  reply_round: MessageSquare,
  situation: Activity,
  phase_change: Activity,
  context_build: Activity,
};

function formatElapsed(startTs: number): string {
  const sec = Math.max(0, Date.now() / 1000 - startTs);
  if (sec < 60) return `${sec.toFixed(0)}s`;
  return `${Math.floor(sec / 60)}m${Math.floor(sec % 60)}s`;
}

/** 实时状态条：显示 AI 当前动作，点击进入思维面板 */
export function StatusBar() {
  const { t } = useTranslation("workbench");
  useThinkingBootstrap();

  const connected = useThinkingStore((s) => s.connected);
  const enabled = useThinkingStore((s) => s.enabled);
  const activeSession = useThinkingStore((s) => s.activeSession);
  const setActiveTab = useWorkbenchStore((s) => s.setActiveTab);

  // 每秒刷新耗时显示
  const [, setTick] = useState(0);
  const hasRunning = activeSession?.nodes.some((n) => n.status === "running") ?? false;
  useEffect(() => {
    if (!hasRunning) return;
    const timer = setInterval(() => setTick((v) => v + 1), 1000);
    return () => clearInterval(timer);
  }, [hasRunning]);

  if (!enabled || !activeSession || activeSession.ended) return null;

  const nodes = activeSession.nodes;
  const runningNode = [...nodes].reverse().find((n) => n.status === "running");
  const errorNodes = nodes.filter((n) => n.status === "error");
  const lastNode: TraceNode | undefined = runningNode ?? nodes[nodes.length - 1];
  if (!lastNode) return null;

  const Icon = TYPE_ICONS[lastNode.type] ?? Activity;
  const hasError = errorNodes.length > 0;

  return (
    <button
      onClick={() => setActiveTab("trace")}
      className={cn(
        "flex items-center gap-2 w-full px-3 py-1.5 mb-2 rounded-md border text-xs transition-colors shrink-0",
        hasError
          ? "border-[rgba(239,68,68,0.4)] bg-danger-subtle text-danger"
          : "border-border bg-elevated text-muted hover:text-foreground",
      )}
    >
      <span className={cn(
        "inline-block w-1.5 h-1.5 rounded-full shrink-0",
        hasError ? "bg-danger" : connected ? "bg-ok animate-pulse" : "bg-muted",
      )} />
      <Icon size={13} className="shrink-0" />
      <span className="truncate flex-1 text-left">{lastNode.label}</span>
      {hasError && (
        <span className="flex items-center gap-1 shrink-0">
          <AlertCircle size={12} />
          {t("statusbar.errors", { count: errorNodes.length })}
        </span>
      )}
      {runningNode && (
        <span className="shrink-0 font-mono">{formatElapsed(runningNode.timestamp)}</span>
      )}
      <ChevronRight size={13} className="shrink-0 opacity-60" />
    </button>
  );
}
