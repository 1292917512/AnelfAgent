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
