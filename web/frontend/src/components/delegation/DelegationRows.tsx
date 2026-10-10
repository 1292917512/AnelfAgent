import { useTranslation } from "react-i18next";
import { Bot, CircleSlash, Loader2, ScrollText, SendHorizontal, Zap } from "lucide-react";
import type { DelegationOverviewItem } from "@/lib/types";
import { formatDuration, formatTokens, scopeAdapter } from "./format";
export function RunningRow({
  item,
  extraSeconds,
  onShowProgress,
  onSteer,
  onCancel,
  cancelling,
}: {
  item: DelegationOverviewItem;
  /** 距上次数据刷新的本地漂移秒数（耗时平滑跳动） */
  extraSeconds: number;
  onShowProgress: () => void;
  onSteer: () => void;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const { t } = useTranslation("plan");
  const adapter = scopeAdapter(item.scope);
  const usage = item.usage ?? {};
  const totalTokens = (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);

  return (
    <div className="delegation-running-row">
      <div className="flex items-center gap-2 min-w-0">
        <Bot size={14} className="shrink-0 text-accent" />
        <span className="text-sm font-medium text-heading flex-1 min-w-0 line-clamp-3 break-words">
          {item.goal || t("delegation.untitled")}
        </span>
        <span className="text-xs text-muted shrink-0 font-mono">
          {formatDuration(item.elapsed_seconds + extraSeconds)}
        </span>
        {item.background && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-subtle text-accent shrink-0">
            {t("delegation.background")}
          </span>
        )}
        {item.role === "orchestrator" && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300 shrink-0">
            <Zap size={9} className="inline -mt-0.5 mr-0.5" />
            {t("delegation.orchestrator")}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1.5 text-[11px] text-muted flex-wrap">
        {item.parent_id && <span title={item.parent_id}>{t("dashboard:history.nested", { depth: item.depth ?? 1 })} · #{item.parent_id.slice(0, 8)}</span>}
        <Loader2 size={11} className="animate-spin shrink-0 text-accent" />
        <span className="truncate">
          {item.state === "queued" ? t("delegation.queued") : item.current_tool
            ? t("delegation.progress.usingTool", { tool: item.current_tool })
            : item.iteration > 0
              ? t("delegation.progress.round", { n: item.iteration })
              : t("delegation.running")}
        </span>
        {item.agent && <span className="text-accent truncate">@{item.agent}</span>}
        {item.model && <span className="truncate">{item.model}</span>}
        {adapter && (
          <span className="px-1.5 py-0.5 rounded bg-accent-subtle text-accent text-[10px]">{adapter}</span>
        )}
        {(usage.turns ?? 0) > 0 && (
          <span>{t("delegation.panel.turns", { n: usage.turns })}</span>
        )}
        {totalTokens > 0 && (
          <span>{t("delegation.panel.tokens", { n: formatTokens(totalTokens) })}</span>
        )}
      </div>
      <div className="delegation-actions">
        <button
          onClick={onShowProgress}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-muted hover:text-foreground hover:bg-hover transition-colors"
        >
          <ScrollText size={11} />
          {t("delegation.panel.progress")}
        </button>
        <button
          onClick={onSteer}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-accent hover:bg-accent-subtle transition-colors"
        >
          <SendHorizontal size={11} />
          {t("delegation.panel.steer")}
        </button>
        <button
          onClick={onCancel}
          disabled={cancelling}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-danger hover:bg-danger-subtle transition-colors disabled:opacity-50"
        >
          <CircleSlash size={11} />
          {cancelling ? t("delegation.cancelling") : t("delegation.panel.stop")}
        </button>
      </div>
    </div>
  );
}
