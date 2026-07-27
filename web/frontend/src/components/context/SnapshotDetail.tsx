import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { ContextSnapshotData, SnapshotSection, SnapshotMessage } from "@/lib/types";
import { ChevronDown, ChevronRight, Copy, Check } from "lucide-react";

const LAYER_COLORS: Record<string, string> = {
  stable: "border-l-violet-500",
  context: "border-l-blue-500",
  volatile: "border-l-amber-500",
  overflow: "border-l-red-500",
  security: "border-l-rose-500",
  memory: "border-l-emerald-500",
  conversation: "border-l-cyan-500",
  tool_chain: "border-l-orange-500",
  exec_context: "border-l-teal-500",
};

const LAYER_BAR_COLORS: Record<string, string> = {
  stable: "bg-violet-500",
  context: "bg-blue-500",
  volatile: "bg-amber-500",
  overflow: "bg-red-500",
  security: "bg-rose-500",
  memory: "bg-emerald-500",
  conversation: "bg-cyan-500",
  tool_chain: "bg-orange-500",
  exec_context: "bg-teal-500",
};

function MessageItem({ msg }: { msg: SnapshotMessage }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const content = typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content, null, 2);
  const isLong = content.length > 500;

  const handleCopy = () => {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="py-2 px-3 rounded-md bg-elevated/50 border border-border/50 text-[11px]">
      <div className="flex items-center gap-1.5">
        <span className={cn(
          "px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide",
          msg.role === "system" ? "bg-violet-500/15 text-violet-400" :
          msg.role === "user" ? "bg-cyan-500/15 text-cyan-400" :
          msg.role === "assistant" ? "bg-emerald-500/15 text-emerald-400" :
          msg.role === "tool" ? "bg-orange-500/15 text-orange-400" :
          "bg-muted/15 text-muted",
        )}>
          {msg.role}
        </span>
        {msg.tool_call_id && (
          <span className="text-[9px] font-mono text-muted truncate">id:{msg.tool_call_id.slice(0, 16)}</span>
        )}
        <span className="flex-1" />
        <span className="text-[9px] text-muted">{content.length} chars</span>
        <button onClick={handleCopy} className="p-0.5 rounded text-muted hover:text-foreground" title="Copy">
          {copied ? <Check size={10} className="text-ok" /> : <Copy size={10} />}
        </button>
      </div>
      {msg.tool_calls && msg.tool_calls.length > 0 && (
        <div className="mt-1 text-[10px] text-orange-400 font-mono">
          tool_calls: {(msg.tool_calls as Record<string, unknown>[]).map((tc) => ((tc.function as Record<string, unknown>)?.name as string) ?? "?").join(", ")}
        </div>
      )}
      <pre className={cn(
        "mt-1.5 whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-foreground/80 bg-panel/50 rounded p-2",
        isLong && !expanded && "line-clamp-6",
      )}>
        {content}
      </pre>
      {isLong && (
        <button onClick={() => setExpanded(!expanded)} className="text-[10px] text-accent hover:underline mt-1">
          {expanded ? "收起" : `展开全部 (${content.length} chars)`}
        </button>
      )}
    </div>
  );
}

function SectionBlock({ section, totalTokens }: { section: SnapshotSection; totalTokens: number }) {
  const [open, setOpen] = useState(false);
  const colorClass = LAYER_COLORS[section.layer] || "border-l-muted";
  const barColor = LAYER_BAR_COLORS[section.layer] || "bg-muted";
  const pct = totalTokens > 0 ? Math.round((section.estimated_tokens / totalTokens) * 100) : 0;

  return (
    <div className={cn("border-l-2 pl-3", colorClass)}>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-2 w-full text-left py-1.5 group">
        {open ? <ChevronDown size={12} className="text-muted" /> : <ChevronRight size={12} className="text-muted" />}
        <span className="text-xs font-medium text-foreground">{section.label}</span>
        <span className="text-[10px] text-muted font-mono">×{section.count}</span>
        <span className="flex-1" />
        <span className="text-[10px] font-mono text-muted">~{section.estimated_tokens}t ({pct}%)</span>
      </button>
      {/* 占比条 */}
      <div className="h-1 rounded-full bg-elevated overflow-hidden mb-1 ml-5">
        <div className={cn("h-full rounded-full", barColor)} style={{ width: `${Math.max(pct, 1)}%` }} />
      </div>
      {open && (
        <div className="space-y-1.5 pb-2 ml-5">
          {section.messages.map((msg, i) => (
            <MessageItem key={i} msg={msg} />
          ))}
        </div>
      )}
    </div>
  );
}

interface SnapshotDetailProps {
  snapshot: ContextSnapshotData;
}

export function SnapshotDetail({ snapshot }: SnapshotDetailProps) {
  const { t } = useTranslation("context");
  const [showTools, setShowTools] = useState(false);

  const ctxWindow = snapshot.model_context_window || 0;
  const usedTokens = snapshot.estimated_tokens || 0;
  const usagePct = ctxWindow > 0 ? Math.round((usedTokens / ctxWindow) * 100) : 0;
  const totalSectionTokens = snapshot.sections.reduce((s, sec) => s + sec.estimated_tokens, 0);

  return (
    <div className="space-y-4">
      {/* 顶部统计 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="p-3 rounded-lg bg-elevated border border-border text-center">
          <p className="text-lg font-bold text-heading font-mono">~{usedTokens.toLocaleString()}</p>
          <p className="text-[10px] text-muted mt-0.5">{t("stats.totalTokens")}</p>
        </div>
        <div className="p-3 rounded-lg bg-elevated border border-border text-center">
          <p className="text-lg font-bold text-heading font-mono">{ctxWindow > 0 ? `${usagePct}%` : "—"}</p>
          <p className="text-[10px] text-muted mt-0.5">{t("stats.modelUsage")}</p>
          {ctxWindow > 0 && (
            <div className="h-1 rounded-full bg-panel mt-1.5 overflow-hidden">
              <div
                className={cn("h-full rounded-full", usagePct >= 90 ? "bg-danger" : usagePct >= 70 ? "bg-warn" : "bg-ok")}
                style={{ width: `${Math.min(usagePct, 100)}%` }}
              />
            </div>
          )}
        </div>
        <div className="p-3 rounded-lg bg-elevated border border-border text-center">
          <p className="text-lg font-bold text-heading font-mono">{snapshot.message_count}</p>
          <p className="text-[10px] text-muted mt-0.5">{t("stats.messages")}</p>
        </div>
        <div className="p-3 rounded-lg bg-elevated border border-border text-center">
          <p className="text-lg font-bold text-heading font-mono">{snapshot.tool_count}</p>
          <p className="text-[10px] text-muted mt-0.5">{t("stats.tools")}</p>
        </div>
      </div>

      {/* 模型信息 */}
      <div className="flex items-center gap-3 text-[11px] text-muted px-1">
        <span className="font-mono text-foreground">{snapshot.model}</span>
        {ctxWindow > 0 && <span>· {ctxWindow.toLocaleString()} ctx</span>}
        <span>· {new Date(snapshot.captured_at * 1000).toLocaleString()}</span>
      </div>

      {/* 工具清单 */}
      <div className="border border-border rounded-lg">
        <button
          onClick={() => setShowTools(!showTools)}
          className="flex items-center gap-2 w-full px-3 py-2 text-xs text-muted hover:text-foreground"
        >
          {showTools ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          <span>{t("tools.title")} ({snapshot.tool_count})</span>
        </button>
        {showTools && (
          <div className="px-3 pb-3 flex flex-wrap gap-1">
            {snapshot.tool_names.map((name) => (
              <span key={name} className="px-1.5 py-0.5 rounded bg-elevated border border-border text-[10px] font-mono text-foreground/70">
                {name}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 分类 sections */}
      <div className="space-y-2">
        <div className="flex items-center justify-between px-1">
          <span className="text-xs font-semibold text-heading">{t("sections.title")}</span>
          <span className="text-[10px] text-muted font-mono">~{totalSectionTokens}t</span>
        </div>
        {snapshot.sections.map((section) => (
          <SectionBlock key={section.layer} section={section} totalTokens={totalSectionTokens} />
        ))}
      </div>
    </div>
  );
}
