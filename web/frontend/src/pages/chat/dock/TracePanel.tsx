import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity, AlertCircle, Brain, CheckCircle2, ChevronDown, ChevronRight,
  Circle, Loader2, MessageSquare, Wrench, Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useThinkingStore, type TraceNode } from "@/stores/thinking-store";
import { useThinkingBootstrap } from "../useThinkingBootstrap";

const TYPE_ICONS: Record<string, typeof Activity> = {
  llm_call: Brain,
  tool_call: Wrench,
  decision: Zap,
  reply_round: MessageSquare,
  situation: Activity,
  phase_change: Activity,
  context_build: Activity,
  introspection: Brain,
};

const STATUS_COLORS: Record<string, string> = {
  running: "text-info",
  completed: "text-ok",
  error: "text-danger",
  pending: "text-muted",
};

function StatusIcon({ status }: { status: TraceNode["status"] }) {
  if (status === "running") return <Loader2 size={12} className="text-info animate-spin shrink-0" />;
  if (status === "completed") return <CheckCircle2 size={12} className="text-ok shrink-0" />;
  if (status === "error") return <AlertCircle size={12} className="text-danger shrink-0" />;
  return <Circle size={12} className="text-muted shrink-0" />;
}

/** 节点 data 摘要（截取关键字段） */
function dataSummary(node: TraceNode): string {
  const d = node.data || {};
  const parts: string[] = [];
  if (typeof d.tool === "string") parts.push(d.tool);
  if (typeof d.error === "string") parts.push(String(d.error).slice(0, 120));
  if (typeof d.preview === "string") parts.push(String(d.preview).slice(0, 80));
  if (typeof d.decision === "string") parts.push(String(d.decision));
  return parts.join(" · ");
}

function NodeRow({ node, depth }: { node: TraceNode; depth: number }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = TYPE_ICONS[node.type] ?? Activity;
  const summary = dataSummary(node);

  return (
    <div>
      <button
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 w-full px-2 py-1 rounded text-left hover:bg-hover transition-colors",
          node.status === "error" && "bg-danger-subtle",
        )}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        {summary
          ? expanded ? <ChevronDown size={11} className="text-muted shrink-0" /> : <ChevronRight size={11} className="text-muted shrink-0" />
          : <span className="w-[11px] shrink-0" />}
        <StatusIcon status={node.status} />
        <Icon size={12} className={cn("shrink-0", STATUS_COLORS[node.status])} />
        <span className={cn("flex-1 truncate text-[11px]", node.status === "error" ? "text-danger" : "text-foreground")}>
          {node.label}
        </span>
        {node.duration_ms != null && (
          <span className="text-[10px] text-muted font-mono shrink-0">
            {node.duration_ms < 1000 ? `${node.duration_ms}ms` : `${(node.duration_ms / 1000).toFixed(1)}s`}
          </span>
        )}
      </button>
      {expanded && summary && (
        <div
          className="mx-2 mb-1 px-2 py-1.5 rounded bg-elevated border border-border text-[10px] text-muted break-all"
          style={{ marginLeft: `${8 + depth * 14 + 11}px` }}
        >
          {summary}
        </div>
      )}
    </div>
  );
}

/** 迷你思维时间线：活跃会话节点的紧凑列表（错误高亮，点击展开摘要） */
export function TracePanel() {
  const { t } = useTranslation("workbench");
  useThinkingBootstrap();
  const activeSession = useThinkingStore((s) => s.activeSession);
  const enabled = useThinkingStore((s) => s.enabled);

  if (!enabled) {
    return <p className="p-3 text-xs text-muted">{t("trace.disabled")}</p>;
  }
  if (!activeSession || activeSession.nodes.length === 0) {
    return <p className="p-3 text-xs text-muted">{t("trace.empty")}</p>;
  }

  // 计算嵌套深度
  const depthOf = (node: TraceNode, all: TraceNode[]): number => {
    let depth = 0;
    let cur = node.parent_id;
    const ids = new Set(all.map((n) => n.id));
    while (cur && ids.has(cur) && depth < 6) {
      depth += 1;
      cur = all.find((n) => n.id === cur)?.parent_id ?? null;
    }
    return depth;
  };

  const nodes = activeSession.nodes;

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-border flex items-center justify-between shrink-0">
        <span className="text-[11px] text-muted">
          {t("trace.sessionInfo", { count: nodes.length })}
          {activeSession.ended && ` · ${t("trace.ended")}`}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {nodes.map((n) => (
          <NodeRow key={n.id} node={n} depth={depthOf(n, nodes)} />
        ))}
      </div>
    </div>
  );
}
