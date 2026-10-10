import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { ContextSnapshotData } from "@/lib/types";
import { SnapshotSectionBlock } from "@/components/common/SnapshotBlocks";
import { ContextWindow } from "./ContextWindow";
import { ChevronDown, ChevronRight, Zap } from "lucide-react";

interface SnapshotDetailProps {
  snapshot: ContextSnapshotData;
}

export function SnapshotDetail({ snapshot }: SnapshotDetailProps) {
  const { t } = useTranslation("context");
  const [showTools, setShowTools] = useState(false);

  const totalSectionTokens = snapshot.sections.reduce((s, sec) => s + sec.estimated_tokens, 0);

  return (
    <div className="space-y-4">
      <ContextWindow snapshot={snapshot} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
        <span className="break-all font-mono text-foreground">{snapshot.model}</span>
        <span>{snapshot.message_count} {t("stats.messages")} · {snapshot.tool_count} {t("stats.tools")}</span>
        <time>{new Date(snapshot.captured_at * 1000).toLocaleString()}</time>
      </div>

      {/* 缓存观测：上次调用真实命中 + 可复用前缀估算 */}
      {snapshot.cache && (() => {
        const lastCall = snapshot.cache.last_call;
        const stale = (lastCall?.age_sec ?? 0) > 120;
        const unobs = !!lastCall?.unobservable;
        const allUnobs = snapshot.cache.recent.unobservable_count === snapshot.cache.recent.sample_count
          && snapshot.cache.recent.sample_count > 0;
        return (
          <div className="p-3 rounded-lg bg-elevated border border-border space-y-2">
            <p className="text-xs font-semibold text-heading">{t("cache.title")}</p>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(105px,1fr))] gap-2 text-center">
              <div>
                <p className={cn("text-sm font-bold font-mono", (stale || unobs) ? "text-muted" : "text-ok")}>
                  {unobs ? "—" : lastCall ? `${Math.round(lastCall.cache_hit_rate * 100)}%` : "—"}
                </p>
                <p className="text-[10px] text-muted mt-0.5">
                  {t("cache.lastHitRate")}
                  {lastCall && stale && !unobs && (
                    <span className="ml-1">{t("cache.staleMark", { min: Math.round((lastCall.age_sec ?? 0) / 60) })}</span>
                  )}
                </p>
              </div>
              <div>
                <p className="text-sm font-bold text-heading font-mono">
                  {lastCall && !unobs ? lastCall.cache_read_input_tokens.toLocaleString() : "—"}
                </p>
                <p className="text-[10px] text-muted mt-0.5">{t("cache.lastReadTokens")}</p>
              </div>
              <div>
                <p className="text-sm font-bold text-heading font-mono">
                  {allUnobs ? "—" : snapshot.cache.recent.sample_count > 0
                    ? `${Math.round(snapshot.cache.recent.avg_cache_hit_rate * 100)}%`
                    : "—"}
                </p>
                <p className="text-[10px] text-muted mt-0.5">
                  {t("cache.avgHitRate", { count: snapshot.cache.recent.sample_count })}
                </p>
              </div>
              <div>
                <p className="text-sm font-bold text-heading font-mono">
                  {snapshot.cache.estimated_cacheable_prefix_tokens != null
                    ? `~${snapshot.cache.estimated_cacheable_prefix_tokens.toLocaleString()}t`
                    : "—"}
                </p>
                <p className="text-[10px] text-muted mt-0.5">{t("cache.stablePrefix")}</p>
              </div>
              <div>
                <p className={cn(
                  "text-sm font-bold font-mono",
                  lastCall && !unobs && snapshot.cache.expected_prefix_tokens != null
                    && lastCall.cache_read_input_tokens < snapshot.cache.expected_prefix_tokens * 0.5
                    ? "text-warn"
                    : "text-heading",
                )}>
                  {snapshot.cache.expected_prefix_tokens != null
                    ? `~${snapshot.cache.expected_prefix_tokens.toLocaleString()}t`
                    : "—"}
                </p>
                <p className="text-[10px] text-muted mt-0.5">{t("cache.expectedPrefix")}</p>
              </div>
            </div>
            {unobs && (
              <p className="text-[10px] text-muted">
                {t("cache.unobservable", { model: lastCall?.model ?? "" })}
              </p>
            )}
            {snapshot.cache.recent.sample_count === 0 && (
              <p className="text-[10px] text-muted">
                {snapshot.cache.recent.no_usage_count > 0
                  ? t("cache.noUsage", { count: snapshot.cache.recent.no_usage_count })
                  : t("cache.noData")}
              </p>
            )}
          </div>
        );
      })()}

      {/* 工具清单 */}
      <div className="border border-border rounded-lg">
        <button
          onClick={() => setShowTools(!showTools)}
          aria-expanded={showTools}
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

      {/* 分类 sections（按 wire 顺序 = 发送给模型的真实顺序） */}
      <div className="space-y-2">
        <div className="flex items-center justify-between px-1">
          <span className="text-xs font-semibold text-heading">{t("sections.title")}</span>
          <span className="text-[10px] text-muted font-mono">~{totalSectionTokens}t</span>
        </div>
        {snapshot.prefix_break && snapshot.prefix_break.layer != null && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-amber-500/40 bg-amber-500/10 text-[11px] text-warn">
            <Zap size={11} className="shrink-0" />
            <span>
              {t("sections.breakBanner", {
                label: snapshot.prefix_break.label ?? snapshot.prefix_break.layer,
                index: snapshot.prefix_break.index ?? 0,
                tokens: (snapshot.prefix_break.before_tokens ?? 0).toLocaleString(),
              })}
            </span>
          </div>
        )}
        {snapshot.prefix_break && snapshot.prefix_break.layer == null && (
          <div className="px-3 py-1.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-[11px] text-ok">
            {t("sections.stableAll")}
          </div>
        )}
        {snapshot.sections.map((section, index) => (
          <SnapshotSectionBlock
            key={`${section.layer}-${index}`}
            section={section}
            totalTokens={totalSectionTokens}
            prefixBreak={snapshot.prefix_break ?? null}
          />
        ))}
      </div>
    </div>
  );
}
