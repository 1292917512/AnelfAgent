/**
 * 上下文快照的共享展示组件（SnapshotDetail / SnapshotPanel 共用）：
 * - LAYER_COLORS / LAYER_BAR_COLORS：layer → 颜色映射
 * - SnapshotMessageItem：单条消息（角色徽章 / 复制 / 长文折叠）
 * - SnapshotSectionBlock：单个 section（可带 token 占比条）
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { SnapshotMessage, SnapshotSection } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { ChevronDown, ChevronRight, Copy, Check } from "lucide-react";

export const LAYER_COLORS: Record<string, string> = {
  stable: "border-l-violet-500",
  context: "border-l-blue-500",
  summary: "border-l-indigo-500",
  conversation: "border-l-cyan-500",
  profile: "border-l-fuchsia-500",
  volatile: "border-l-amber-500",
  memory: "border-l-emerald-500",
  provider: "border-l-sky-500",
  overflow: "border-l-red-500",
  security: "border-l-rose-500",
  tool_chain: "border-l-orange-500",
  exec_context: "border-l-teal-500",
};

export const LAYER_BAR_COLORS: Record<string, string> = {
  stable: "bg-violet-500",
  context: "bg-blue-500",
  summary: "bg-indigo-500",
  conversation: "bg-cyan-500",
  profile: "bg-fuchsia-500",
  volatile: "bg-amber-500",
  memory: "bg-emerald-500",
  provider: "bg-sky-500",
  overflow: "bg-red-500",
  security: "bg-rose-500",
  tool_chain: "bg-orange-500",
  exec_context: "bg-teal-500",
};

interface SnapshotMessageItemProps {
  msg: SnapshotMessage;
  /** 超过该字符数折叠（card=500 / inline=300） */
  longThreshold?: number;
  /** tool_call_id 展示截断长度（card=16 / inline=12） */
  idLength?: number;
  /** card：SnapshotDetail 的卡片式；inline：SnapshotPanel 的紧凑式 */
  variant?: "card" | "inline";
}

/** 快照消息中 tool_calls 的结构（OpenAI 风格 function calling） */
interface SnapshotToolCall {
  function?: { name?: string };
}

export function SnapshotMessageItem({
  msg,
  longThreshold = 500,
  idLength = 16,
  variant = "card",
}: SnapshotMessageItemProps) {
  const { t } = useTranslation("common");
  const [expanded, setExpanded] = useState(false);
  const [copied, triggerCopied] = useCopyFeedback(1500);
  const content = typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content, null, 2);
  const isLong = content.length > longThreshold;

  const handleCopy = () => {
    navigator.clipboard.writeText(content).then(triggerCopied).catch(() => { /* 剪贴板不可用 */ });
  };

  return (
    <div className={cn(
      "bg-elevated/50 border border-border/50 text-[11px]",
      variant === "card" ? "py-2 px-3 rounded-md" : "py-1.5 px-2 rounded-sm",
    )}>
      <div className="flex items-center gap-1.5">
        <span className={cn(
          "py-0.5 rounded text-[9px] font-bold uppercase tracking-wide",
          variant === "card" ? "px-1.5" : "px-1",
          msg.role === "system" ? "bg-violet-500/15 text-violet-400" :
          msg.role === "user" ? "bg-cyan-500/15 text-cyan-400" :
          msg.role === "assistant" ? "bg-emerald-500/15 text-emerald-400" :
          msg.role === "tool" ? "bg-orange-500/15 text-orange-400" :
          "bg-muted/15 text-muted",
        )}>
          {msg.role}
        </span>
        {msg.tool_call_id && (
          <span className="text-[9px] font-mono text-muted truncate">id:{msg.tool_call_id.slice(0, idLength)}</span>
        )}
        <span className="flex-1" />
        <span className="text-[9px] text-muted">{content.length} chars</span>
        <button onClick={handleCopy} className="p-0.5 rounded text-muted hover:text-foreground" title={t("copy")} aria-label={t("copy")}>
          {copied ? <Check size={10} className="text-ok" /> : <Copy size={10} />}
        </button>
      </div>
      {msg.tool_calls && msg.tool_calls.length > 0 && (
        <div className="mt-1 text-[10px] text-orange-400 font-mono">
          tool_calls: {(msg.tool_calls as SnapshotToolCall[]).map((tc) => tc.function?.name ?? "?").join(", ")}
        </div>
      )}
      <pre className={cn(
        "whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-foreground/80",
        variant === "card" ? "mt-1.5 bg-panel/50 rounded p-2" : "mt-1",
        isLong && !expanded && (variant === "card" ? "line-clamp-6" : "line-clamp-4"),
      )}>
        {content}
      </pre>
      {isLong && (
        <button
          onClick={() => setExpanded(!expanded)}
          className={cn("text-[10px] text-accent hover:underline", variant === "card" ? "mt-1" : "mt-0.5")}
        >
          {expanded ? t("collapse") : t("expandAll", { count: content.length })}
        </button>
      )}
    </div>
  );
}

interface SnapshotSectionBlockProps {
  section: SnapshotSection;
  /** >0 时展示 token 数与占比条（SnapshotDetail）；缺省不展示（SnapshotPanel） */
  totalTokens?: number;
  /** 初始展开状态 */
  defaultOpen?: boolean;
}

export function SnapshotSectionBlock({ section, totalTokens, defaultOpen = false }: SnapshotSectionBlockProps) {
  const { t } = useTranslation("context");
  const [open, setOpen] = useState(defaultOpen);
  const colorClass = LAYER_COLORS[section.layer] || "border-l-muted";
  const showTokens = typeof totalTokens === "number" && totalTokens > 0;
  const barColor = LAYER_BAR_COLORS[section.layer] || "bg-muted";
  const pct = showTokens ? Math.round((section.estimated_tokens / totalTokens) * 100) : 0;

  return (
    <div className={cn("border-l-2 pl-3", colorClass)}>
      <button
        onClick={() => setOpen(!open)}
        className={cn("flex items-center gap-2 w-full text-left group", showTokens ? "py-1.5" : "py-1")}
      >
        {open ? <ChevronDown size={12} className="text-muted" /> : <ChevronRight size={12} className="text-muted" />}
        <span className="text-xs font-medium text-foreground">{section.label}</span>
        <span className="text-[10px] text-muted font-mono">×{section.count}</span>
        {section.volatility_label && (
          <span
            className="px-1 py-px rounded text-[9px] bg-sky-500/10 text-sky-500"
            title={t("sections.volatilityTitle", { value: section.volatility })}
          >
            {section.volatility_label}
          </span>
        )}
        {section.changed === true && (
          <span className="px-1 py-px rounded text-[9px] font-medium bg-amber-500/15 text-amber-500">
            {t("sections.changed")}
          </span>
        )}
        {section.changed === false && (
          <span className="px-1 py-px rounded text-[9px] font-medium bg-emerald-500/15 text-emerald-500">
            {t("sections.unchanged")}
          </span>
        )}
        {showTokens && (
          <>
            <span className="flex-1" />
            <span className="text-[10px] font-mono text-muted">~{section.estimated_tokens}t ({pct}%)</span>
          </>
        )}
      </button>
      {showTokens && (
        <div className="h-1 rounded-full bg-elevated overflow-hidden mb-1 ml-5">
          <div className={cn("h-full rounded-full", barColor)} style={{ width: `${Math.max(pct, 1)}%` }} />
        </div>
      )}
      {open && (
        <div className={cn("pb-2", showTokens ? "space-y-1.5 ml-5" : "space-y-1")}>
          {section.messages.map((msg, i) => (
            <SnapshotMessageItem
              key={i}
              msg={msg}
              variant={showTokens ? "card" : "inline"}
              longThreshold={showTokens ? 500 : 300}
              idLength={showTokens ? 16 : 12}
            />
          ))}
        </div>
      )}
    </div>
  );
}
