import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { ContextSnapshotData } from "@/lib/types";
import { SnapshotSectionBlock } from "@/components/common/SnapshotBlocks";
import { ChevronDown, ChevronRight } from "lucide-react";

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

      {/* 缓存观测：上次调用真实命中 + 可复用前缀估算 */}
      {snapshot.cache && (() => {
        const lastCall = snapshot.cache.last_call;
        const stale = (lastCall?.age_sec ?? 0) > 120;
        return (
          <div className="p-3 rounded-lg bg-elevated border border-border space-y-2">
            <p className="text-xs font-semibold text-heading">{t("cache.title")}</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
              <div>
                <p className={cn("text-sm font-bold font-mono", stale ? "text-muted" : "text-emerald-500")}>
                  {lastCall
                    ? `${Math.round(lastCall.cache_hit_rate * 100)}%`
                    : "—"}
                </p>
                <p className="text-[10px] text-muted mt-0.5">
                  {t("cache.lastHitRate")}
                  {lastCall && stale && (
                    <span className="ml-1">{t("cache.staleMark", { min: Math.round((lastCall.age_sec ?? 0) / 60) })}</span>
                  )}
                </p>
              </div>
              <div>
                <p className="text-sm font-bold text-heading font-mono">
                  {lastCall
                    ? lastCall.cache_read_input_tokens.toLocaleString()
                    : "—"}
                </p>
                <p className="text-[10px] text-muted mt-0.5">{t("cache.lastReadTokens")}</p>
              </div>
              <div>
                <p className="text-sm font-bold text-heading font-mono">
                  {snapshot.cache.recent.sample_count > 0
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
            </div>
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
          <SnapshotSectionBlock key={section.layer} section={section} totalTokens={totalSectionTokens} />
        ))}
      </div>
    </div>
  );
}
