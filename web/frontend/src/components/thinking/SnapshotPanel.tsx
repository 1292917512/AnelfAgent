import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { ContextSnapshotData, SnapshotSection, SnapshotMessage } from "@/lib/types";
import { ChevronDown, ChevronRight, Copy, Check, X } from "lucide-react";

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

function MessageItem({ msg }: { msg: SnapshotMessage }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const content = typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content, null, 2);
  const isLong = content.length > 300;

  const handleCopy = () => {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="py-1.5 px-2 rounded-sm bg-elevated/50 border border-border/50 text-[11px]">
      <div className="flex items-center gap-1.5">
        <span className={cn(
          "px-1 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide",
          msg.role === "system" ? "bg-violet-500/15 text-violet-400" :
          msg.role === "user" ? "bg-cyan-500/15 text-cyan-400" :
          msg.role === "assistant" ? "bg-emerald-500/15 text-emerald-400" :
          msg.role === "tool" ? "bg-orange-500/15 text-orange-400" :
          "bg-muted/15 text-muted",
        )}>
          {msg.role}
        </span>
        {msg.tool_call_id && (
          <span className="text-[9px] font-mono text-muted truncate">id:{msg.tool_call_id.slice(0, 12)}</span>
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
      <pre
        className={cn(
          "mt-1 whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-foreground/80",
          isLong && !expanded && "line-clamp-4",
        )}
      >
        {content}
      </pre>
      {isLong && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-[10px] text-accent hover:underline mt-0.5"
        >
          {expanded ? "收起" : `展开全部 (${content.length} chars)`}
        </button>
      )}
    </div>
  );
}

function SectionBlock({ section }: { section: SnapshotSection }) {
  const [open, setOpen] = useState(section.layer === "conversation" || section.layer === "tool_chain");
  const colorClass = LAYER_COLORS[section.layer] || "border-l-muted";

  return (
    <div className={cn("border-l-2 pl-3", colorClass)}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full text-left py-1 group"
      >
        {open ? <ChevronDown size={12} className="text-muted" /> : <ChevronRight size={12} className="text-muted" />}
        <span className="text-xs font-medium text-foreground">{section.label}</span>
        <span className="text-[10px] text-muted font-mono">×{section.count}</span>
      </button>
      {open && (
        <div className="space-y-1 pb-2">
          {section.messages.map((msg, i) => (
            <MessageItem key={i} msg={msg} />
          ))}
        </div>
      )}
    </div>
  );
}

interface SnapshotPanelProps {
  snapshot: ContextSnapshotData;
  onClose: () => void;
}

export function SnapshotPanel({ snapshot, onClose }: SnapshotPanelProps) {
  const { t } = useTranslation("thinking");
  const [showTools, setShowTools] = useState(false);

  return (
    <div className="h-full flex flex-col">
      {/* 头部 */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div>
          <span className="text-xs font-semibold text-heading">{t("snapshot.title")}</span>
          <div className="flex items-center gap-2 mt-0.5 text-[10px] text-muted">
            <span className="font-mono">{snapshot.model}</span>
            <span>·</span>
            <span>{snapshot.message_count} msgs</span>
            <span>·</span>
            <span>{snapshot.tool_count} tools</span>
            <span>·</span>
            <span>{new Date(snapshot.captured_at * 1000).toLocaleTimeString()}</span>
          </div>
        </div>
        <button onClick={onClose} className="p-1 rounded-sm text-muted hover:text-foreground hover:bg-hover">
          <X size={14} />
        </button>
      </div>

      {/* 工具列表折叠 */}
      <div className="px-3 py-1.5 border-b border-border">
        <button
          onClick={() => setShowTools(!showTools)}
          className="flex items-center gap-2 text-[11px] text-muted hover:text-foreground"
        >
          {showTools ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          <span>{t("snapshot.tools")} ({snapshot.tool_count})</span>
        </button>
        {showTools && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {snapshot.tool_names.map((name) => (
              <span key={name} className="px-1.5 py-0.5 rounded bg-elevated border border-border text-[10px] font-mono text-foreground/70">
                {name}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 分类 sections */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {snapshot.sections.map((section) => (
          <SectionBlock key={section.layer} section={section} />
        ))}
      </div>
    </div>
  );
}
