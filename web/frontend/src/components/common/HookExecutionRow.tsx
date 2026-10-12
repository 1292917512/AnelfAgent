import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  ChevronDown,
  CircleAlert,
  Loader2,
  Webhook,
} from "lucide-react";
import { useNow } from "@/hooks/useNow";
import type { HookExecution } from "@/lib/types/hooks";
import { Badge } from "@/components/ui";
import { cn } from "@/lib/utils";

/** 同一执行记录在钩子页和工作区中共享状态、耗时与详情展示。 */
export function HookExecutionRow({ run }: { run: HookExecution }) {
  const { t } = useTranslation("settings");
  const [expanded, setExpanded] = useState(false);
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
            : `${(duration / 1000).toFixed(1)}s`}
        </span>
        <ChevronDown
          size={14}
          className={cn(
            "shrink-0 text-muted transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>
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
          <p className="whitespace-pre-wrap break-words leading-relaxed text-foreground">
            {run.detail || t("hookFlow.noDetail")}
          </p>
          <p className="break-all font-mono text-[10px] text-muted">{run.id}</p>
        </div>
      )}
    </div>
  );
}
