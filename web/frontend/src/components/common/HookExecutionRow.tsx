import { useContext, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  ChevronDown,
  CircleAlert,
  Loader2,
  Webhook,
  ArrowUpRight,
} from "lucide-react";
import { useNow } from "@/hooks/useNow";
import type { HookExecution } from "@/lib/types/hooks";
import { Badge, Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { formatElapsedCompact } from "@/lib/format";
import { ActivityInspection } from "./ActivityInspection";
import { HookJudgmentRow } from "./HookJudgmentRow";

/** 同一执行记录在钩子页和工作区中共享状态、耗时与详情展示。 */
export function HookExecutionRow({ run }: { run: HookExecution }) {
  const { t } = useTranslation("settings");
  const [expanded, setExpanded] = useState(false);
  const inspect = useContext(ActivityInspection);
  const activityIds = run.activity_ids ?? [];
  const running = run.status === "running";
  const now = useNow(running);
  const failed = run.status === "error" || run.status === "blocked";
  const duration = running
    ? Math.max(0, now - run.started_at * 1000)
    : run.duration_ms;
  const Status = running
    ? Loader2
    : failed
      ? CircleAlert
      : run.status === "done"
        ? Check
        : Webhook;
  return (
    <div
      className={cn(
        "rounded-xl border border-border/70 bg-card/50",
        failed && "border-warn/30",
      )}
    >
      <button
        type="button"
        className="flex min-h-12 w-full items-center gap-2.5 px-3 py-2 text-left"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        <Status
          size={15}
          className={cn(
            "shrink-0",
            running
              ? "animate-spin text-accent"
              : failed
                ? "text-warn"
                : "text-muted",
          )}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-heading">
            {t(`hookFlow.names.${run.name}`, { defaultValue: run.name })}
          </span>
          <span className="text-xs text-muted">
            {t(`hookFlow.events.${run.event}`, { defaultValue: run.event })}
          </span>
        </span>
        <Badge variant={run.mode === "sync" ? "neutral" : "info"}>
          {t(`hookFlow.${run.mode}`)}
        </Badge>
        <span className="shrink-0 text-xs tabular-nums text-muted">
          {duration < 1000
            ? `${Math.round(duration)}ms`
            : formatElapsedCompact(duration)}
        </span>
        <ChevronDown
          size={14}
          className={cn(
            "shrink-0 text-muted transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>
      {!!run.judgments?.length && <div className="space-y-2 px-3 pb-3">
        {run.judgments.map((judgment) => <HookJudgmentRow key={judgment.id} judgment={judgment} />)}
      </div>}
      {expanded && (
        <div className="space-y-2 border-t border-border/60 px-3 py-3 text-xs">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted">
            <span>
              {t(`hookFlow.status.${run.status}`, { defaultValue: run.status })}
            </span>
            <span>{run.source}</span>
            <span className="break-all">{run.scope}</span>
            <time>{new Date(run.started_at * 1000).toLocaleTimeString()}</time>
          </div>
          {run.context_mode && <p className="text-muted">{t(`hookFlow.context.${run.context_mode}`, { count: run.context_messages ?? 0 })}</p>}
          {run.phase && <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg bg-elevated/60 px-3 py-2.5">
            <span className="font-medium text-foreground">{t(`hookFlow.phases.${run.phase}`, { defaultValue: t(`hookFlow.status.${run.status}`) })}</span>
            {run.operation && <code className="break-all text-accent">{run.operation}</code>}
            <span className="text-muted">{run.model_calls != null && run.tool_calls != null
              ? t("hookFlow.calls", { models: run.model_calls, tools: run.tool_calls })
              : t("hookFlow.callsUntracked")}</span>
          </div>}
          <p className="whitespace-pre-wrap break-words leading-relaxed text-foreground">
            {run.detail || t("hookFlow.noDetail")}
          </p>
          {activityIds.length > 0 && inspect && <Button variant="secondary" size="sm" onClick={() => inspect(activityIds)}><ArrowUpRight size={14} />{t("hookFlow.executionDetails")}</Button>}
          <p className="break-all font-mono text-[10px] text-muted">{run.task_id ? `task:${run.task_id} · ` : ""}{run.id}</p>
        </div>
      )}
    </div>
  );
}
