import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity, AlertCircle, Brain, CheckCircle2, ChevronDown, ChevronRight,
  Circle, Loader2, MessageSquare, Wrench, Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { thinkingApi } from "@/lib/api";
import { useThinkingStore, type SessionSummary, type TraceNode } from "@/stores/thinking-store";
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
  const { t } = useTranslation("workbench");
  const [expanded, setExpanded] = useState(false);
  const Icon = TYPE_ICONS[node.type] ?? Activity;
  const summary = dataSummary(node);
  // LLM 节点的思考全文（有界截断驻留，tracer 内存会话滚动销毁）
  const reasoning = typeof node.data?.reasoning_content === "string" ? node.data.reasoning_content : "";
  const reasoningTruncated = node.data?.reasoning_truncated === true;
  const expandable = Boolean(summary) || Boolean(reasoning);

  return (
    <div>
      <button
        onClick={() => expandable && setExpanded((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 w-full px-2 py-1 rounded text-left transition-colors",
          expandable && "hover:bg-hover",
          node.status === "error" && "bg-danger-subtle",
        )}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        {expandable
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
      {expanded && (
        <div
          className="mx-2 mb-1 space-y-1"
          style={{ marginLeft: `${8 + depth * 14 + 11}px` }}
        >
          {summary && (
            <div className="px-2 py-1.5 rounded bg-elevated border border-border text-[10px] text-muted break-all">
              {summary}
            </div>
          )}
          {reasoning && (
            <div className="rounded bg-elevated border border-border overflow-hidden">
              <div className="px-2 py-1 border-b border-border/50 text-[9px] text-muted flex items-center justify-between">
                <span>{t("trace.reasoning")}</span>
                {reasoningTruncated && (
                  <span className="text-amber-600 dark:text-amber-400">{t("trace.reasoningTruncated")}</span>
                )}
              </div>
              <div className="max-h-64 overflow-y-auto px-2 py-1.5 text-[10px] text-muted whitespace-pre-wrap break-words leading-relaxed">
                {reasoning}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 会话类型徽标文案键（workbench trace.kind.*） */
function sessionKindKey(s: SessionSummary): string {
  if (s.is_delegation) return "trace.kindDelegation";
  if (s.is_introspection) return "trace.kindIntrospection";
  if (s.is_heartbeat) return "trace.kindHeartbeat";
  return "trace.kindChat";
}

function sessionOptionLabel(s: SessionSummary, kindText: string): string {
  const time = new Date(s.start_time * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const tail = s.ended ? "" : " …";
  return `${kindText} ${time} · ${s.node_count}${tail}`;
}

/** 迷你思维时间线：会话切换 + 活跃会话节点的紧凑列表（错误高亮，点击展开摘要/思考） */
export function TracePanel() {
  const { t } = useTranslation("workbench");
  useThinkingBootstrap();
  const sessions = useThinkingStore((s) => s.sessions);
  const activeSessionId = useThinkingStore((s) => s.activeSessionId);
  const activeSession = useThinkingStore((s) => s.activeSession);
  const enabled = useThinkingStore((s) => s.enabled);

  if (!enabled) {
    return <p className="p-3 text-xs text-muted">{t("trace.disabled")}</p>;
  }

  /** 切换会话：历史会话经 REST 拉全量节点（活跃会话随后仍由 SSE 增量驱动） */
  const switchSession = (id: string) => {
    if (!id || id === activeSessionId) return;
    useThinkingStore.getState().setActiveSessionId(id);
    thinkingApi.session(id).then((r) => {
      // 切换竞态：用户可能已再次切走，仅当仍选中该会话时落地
      if (r.data && !r.data.error && useThinkingStore.getState().activeSessionId === id) {
        useThinkingStore.getState().setActiveSession(r.data);
      }
    }).catch(() => {});
  };

  const nodes = activeSession?.nodes ?? [];

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

  return (
    <div className="flex flex-col h-full">
      {/* 会话切换器：心跳/内省不会自动顶号，看历史后台会话从这里进 */}
      {sessions.length > 0 && (
        <div className="px-2 py-1.5 border-b border-border shrink-0">
          <select
            value={activeSessionId ?? ""}
            onChange={(e) => switchSession(e.target.value)}
            className="w-full px-1.5 py-1 text-[11px] bg-card border border-border rounded outline-none focus:border-accent text-foreground"
          >
            {activeSessionId === null && <option value="">{t("trace.pickSession")}</option>}
            {sessions.slice(0, 20).map((s) => (
              <option key={s.id} value={s.id}>
                {sessionOptionLabel(s, t(sessionKindKey(s)))}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="px-3 py-2 border-b border-border flex items-center justify-between shrink-0">
        <span className="text-[11px] text-muted">
          {activeSession
            ? t("trace.sessionInfo", { count: nodes.length }) + (activeSession.ended ? ` · ${t("trace.ended")}` : "")
            : t("trace.empty")}
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
