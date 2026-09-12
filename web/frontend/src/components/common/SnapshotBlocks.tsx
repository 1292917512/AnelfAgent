/**
 * 上下文快照的共享展示组件（SnapshotDetail / SnapshotPanel 共用）：
 * - LAYER_COLORS / LAYER_BAR_COLORS：layer → 颜色映射
 * - SnapshotMessageItem：单条消息（角色徽章 / 复制 / 长文折叠）
 * - SnapshotSectionBlock：单个 section（可带 token 占比条）
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { SnapshotMessage, SnapshotSection, SnapshotPrefixBreak } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { ChevronDown, ChevronRight, Copy, Check, Zap } from "lucide-react";

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
  /** 全局前缀断链点（本层命中时在分歧位置渲染标记行） */
  prefixBreak?: SnapshotPrefixBreak | null;
}

export function SnapshotSectionBlock({ section, totalTokens, defaultOpen = false, prefixBreak }: SnapshotSectionBlockProps) {
  const { t } = useTranslation("context");
  const [open, setOpen] = useState(defaultOpen);
  const [stableOpen, setStableOpen] = useState(false);
  const colorClass = LAYER_COLORS[section.layer] || "border-l-muted";
  const showTokens = typeof totalTokens === "number" && totalTokens > 0;
  const barColor = LAYER_BAR_COLORS[section.layer] || "bg-muted";
  const pct = showTokens ? Math.round((section.estimated_tokens / totalTokens) * 100) : 0;

  // 区块内静态/动态切分：仅混合层（既有稳定前缀又有新增）分段呈现，
  // 完全稳定/完全新增的层保持直接渲染；层收缩（stable==count 但整层变化，
  // 如压缩删尾）按 changed 徽标呈现
  const hasBaseline = section.stable_count != null;
  const stableCount = section.stable_count ?? 0;
  const newCount = section.new_count ?? 0;
  const splitable = hasBaseline && stableCount > 0 && newCount > 0;
  const stableMsgs = splitable ? section.messages.slice(0, stableCount) : [];
  const newMsgs = splitable ? section.messages.slice(stableCount) : section.messages;
  const isBreakLayer = prefixBreak?.layer === section.layer;
  const fullyStable = hasBaseline && newCount === 0 && section.changed === false;
  // 层收缩（断链索引 >= 本层消息数）时分叉在层末，标记渲染于消息之后
  const breakAtTail = isBreakLayer && !splitable
    && (prefixBreak?.index ?? 0) >= section.messages.length;

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
        {hasBaseline && newCount > 0 ? (
          <>
            {splitable && (
              <span className="px-1 py-px rounded text-[9px] font-medium bg-emerald-500/15 text-emerald-500">
                {t("sections.cachedPrefix", { count: stableCount })}
              </span>
            )}
            <span className="px-1 py-px rounded text-[9px] font-medium bg-amber-500/15 text-amber-500">
              {t("sections.newAdds", { count: newCount })}
            </span>
          </>
        ) : fullyStable ? (
          <span className="px-1 py-px rounded text-[9px] font-medium bg-emerald-500/15 text-emerald-500">
            {t("sections.unchanged")}
          </span>
        ) : (
          section.changed === true && (
            <span className="px-1 py-px rounded text-[9px] font-medium bg-amber-500/15 text-amber-500">
              {t("sections.changed")}
            </span>
          )
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
          {/* 静态前缀：与上次快照逐字节一致（默认折叠，缓存可命中区） */}
          {stableMsgs.length > 0 && (
            <div>
              <button
                onClick={() => setStableOpen(!stableOpen)}
                className="flex items-center gap-1 text-[10px] text-emerald-500/80 hover:text-emerald-500"
              >
                {stableOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                {t("sections.cachedPrefixRegion", { count: stableMsgs.length })}
              </button>
              {stableOpen && (
                <div className="space-y-1 mt-1 opacity-70">
                  {stableMsgs.map((msg, i) => (
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
          )}
          {/* 全局断链点标记：本轮缓存命中至此为止 */}
          {isBreakLayer && !breakAtTail && (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded border border-amber-500/40 bg-amber-500/10 text-[10px] text-amber-500">
              <Zap size={10} className="shrink-0" />
              <span className="font-semibold">{t("sections.breakPoint")}</span>
              <span className="text-amber-500/80">
                {t("sections.breakDesc", { tokens: (prefixBreak?.before_tokens ?? 0).toLocaleString() })}
              </span>
            </div>
          )}
          {newMsgs.map((msg, i) => (
            <SnapshotMessageItem
              key={stableMsgs.length + i}
              msg={msg}
              variant={showTokens ? "card" : "inline"}
              longThreshold={showTokens ? 500 : 300}
              idLength={showTokens ? 16 : 12}
            />
          ))}
          {breakAtTail && (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded border border-amber-500/40 bg-amber-500/10 text-[10px] text-amber-500">
              <Zap size={10} className="shrink-0" />
              <span className="font-semibold">{t("sections.breakPoint")}</span>
              <span className="text-amber-500/80">
                {t("sections.breakDesc", { tokens: (prefixBreak?.before_tokens ?? 0).toLocaleString() })}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
