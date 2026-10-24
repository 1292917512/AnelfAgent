import { AlertCircle, Brain, CheckCircle2, ChevronDown, Loader2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ActivityEntry } from "@/lib/types/activity";
import { useNow } from "@/hooks/useNow";
import { formatElapsedCompact } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ActivityInspector } from "./ActivityInspector";
import { renderTaggedText } from "./ActivityReferences";
import { Markdown } from "../render/Markdown";
import type { ActivityThought } from "./activity-stream";

/** 模型调用耗时与供应商实际报告的用量。 */
export function ActivityModel({ entry, thoughts = [] }: { entry: Extract<ActivityEntry, { kind: "model" }>; thoughts?: ActivityThought[] }) {
  const { t } = useTranslation("workbench");
  const running = entry.status === "running";
  const [selection, setSelection] = useState<boolean | null>(null);
  const open = selection ?? running;
  const now = useNow(running);
  const usage = entry.usage;
  const input = usage?.total_input_tokens;
  const cached = usage?.cache_read_input_tokens;
  const rate = usage?.cache_observable && input != null && input > 0 && cached != null
    ? Math.min(100, Math.max(0, Math.round(cached / input * 100))) : null;
  const first = entry.ttft_ms;
  const elapsed = entry.duration_ms;
  const timing = first != null && Number.isFinite(first) && first >= 0 && elapsed != null && elapsed >= first;
  const metrics = <div className="activity-inspection-meta">
    {timing && <><span>{t("activity.firstResponse", { duration: formatElapsedCompact(first) })}</span>
      <span>{t("activity.afterResponse", { duration: formatElapsedCompact(elapsed - first) })}</span></>}
    {input != null && <span>{t("activity.modelUsage", { input: input.toLocaleString(), output: (usage?.completion_tokens ?? 0).toLocaleString() })}</span>}
    {rate != null && cached != null && <span>{t("activity.cachedTokens", { tokens: cached.toLocaleString(), percent: rate })}</span>}
  </div>;
  const summary = <>
    <span>{running ? <Loader2 size={12} className="animate-spin" /> : entry.status === "error" ? <AlertCircle size={12} className="text-warn" /> : <Brain size={12} />}<span className="activity-model-name" title={entry.name}>{entry.name}</span></span>
    <span className="activity-model-state" data-status={entry.status}>
      {entry.status === "done" && <CheckCircle2 size={12} aria-hidden="true" />}
      {t(running ? "activity.thinking" : entry.status === "done" ? thoughts.length ? "activity.thoughtComplete" : "activity.modelComplete" : `activity.status.${entry.status}`, { defaultValue: entry.status })}
    </span>
    <span className="activity-model-duration">{formatElapsedCompact(Math.max(0, running ? now - entry.ts * 1000 : entry.duration_ms ?? 0))}</span>
    {input != null && <span>{t("activity.modelUsage", { input: input.toLocaleString(), output: (usage?.completion_tokens ?? 0).toLocaleString() })}</span>}
    {!running && <span title={t("activity.cacheHint")}>{rate != null ? t("activity.modelCache", { percent: rate }) : t("activity.cacheUnknown")}</span>}
  </>;
  return <div className={cn("activity-model-call", running && "is-live")} data-status={entry.status}>
    {entry.purpose === "compress" && <p className="activity-note">{t("activity.phases.context_summary")}</p>}
    <div className="activity-entry-heading">
      {thoughts.length ? <button className="activity-model activity-model-toggle" aria-expanded={open}
        onClick={() => setSelection(!open)}>
        {summary}<ChevronDown size={13} className={cn("ml-auto shrink-0", open && "rotate-180")} />
      </button> : <div className="activity-model">{summary}</div>}
      <ActivityInspector title={`${entry.name} · ${t("activity.modelDetails")}`} preview={metrics}>
        {metrics}
        <p className="activity-note">{t("activity.timingHint")}</p>
        <p className="activity-note">{t("activity.cacheHint")}</p>
        {thoughts.map((thought) => <div key={thought.id}>
          {thought.truncated && <p className="activity-note">{t("activity.truncated")}</p>}
          <Markdown content={thought.content} renderText={renderTaggedText} live={running} />
        </div>)}
      </ActivityInspector>
    </div>
    {entry.error && <p className="activity-note text-warn" role="alert">{entry.error}</p>}
    {open && thoughts.map((thought) => <div key={thought.id} className="activity-thought-content">
      {thought.truncated && <p className="activity-note">{t("activity.truncated")}</p>}
      <Markdown content={thought.content} renderText={renderTaggedText} live={running} />
    </div>)}
  </div>;
}
