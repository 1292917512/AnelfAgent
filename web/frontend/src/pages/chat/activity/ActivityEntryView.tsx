import { Brain, Check, ChevronDown, Loader2, AlertCircle, Bot, ListChecks, ArrowUpRight } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ActivityEntry } from "@/lib/types/activity";
import { formatElapsedCompact } from "@/lib/format";
import { useNow } from "@/hooks/useNow";
import { Markdown } from "../render/Markdown";
import { DiffView } from "../DiffView";
import { cn } from "@/lib/utils";
import { ActivityTargets, TaggedText, renderTaggedText } from "./ActivityReferences";
import { PlanMeta } from "@/components/plan/PlanMeta";
import { ActivityContext } from "./ActivityContext";
import { ActivityModel } from "./ActivityModel";
import { ActivityToolResult } from "./ActivityToolResult";

function readableJson(value: string): string {
  try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
}

function durationLabel(ms: number): string {
  return ms < 1000 ? `${Math.max(0, Math.round(ms))}ms` : formatElapsedCompact(ms);
}

/** 思考正文、工具状态和文件改动的统一过程行。 */
export function ActivityEntryView({ entry, live, onReveal }: { entry: ActivityEntry; live: boolean; onReveal: (id: string) => void }) {
  const { t } = useTranslation("workbench");
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const now = useNow("status" in entry && (entry.status === "running" || entry.status === "queued"));
  if (entry.kind === "context") return <ActivityContext entry={entry} />;
  if (entry.kind === "file") return <DiffView {...entry} />;
  if (entry.kind === "model") return <ActivityModel entry={entry} />;
  if (entry.kind === "text") return <div className="activity-output">{entry.truncated && <p className="activity-note">{t("activity.truncated")}</p>}<Markdown content={entry.content} renderText={renderTaggedText} /></div>;
  if (entry.kind === "thinking") {
    const open = expanded ?? false;
    return <div className={cn("activity-thought", live && "is-live")}>
      <button aria-expanded={open} onClick={() => setExpanded(!open)} className="activity-entry-toggle"><Brain size={15} /><span>{t(live ? "activity.thinking" : "activity.thought")}</span><ChevronDown size={14} className={cn("ml-auto transition-transform", open && "rotate-180")} /></button>
      <div className={cn("activity-thought-content", !open && "activity-thought-preview")}>{entry.truncated && open && <p className="activity-note">{t("activity.truncated")}</p>}<TaggedText content={entry.content} /></div>
    </div>;
  }
  if (entry.kind === "plan") {
    const done = entry.steps.filter((step) => step.status === "completed").length;
    const open = expanded ?? entry.status === "running";
    return <div className="activity-plan"><button className="activity-entry-toggle" aria-expanded={open} onClick={() => setExpanded(!open)}><ListChecks size={15} className="text-accent" /><span className="min-w-0 flex-1 truncate">{entry.goal}</span><span className="text-xs tabular-nums text-muted">{done}/{entry.steps.length}</span><ChevronDown size={14} className={cn(open && "rotate-180")} /></button>
      {open && <><ol className="activity-plan-steps">{entry.steps.map((step, index) => <li key={index} data-status={step.status}>{step.status === "completed" ? <Check size={14} /> : step.status === "in_progress" && entry.status === "running" ? <Loader2 size={14} className="animate-spin text-accent" /> : <span className="activity-step-number">{index + 1}</span>}<div><TaggedText content={step.content} />{step.note && <p>{step.note}</p>}</div></li>)}</ol>{(entry.files || entry.risks) && <div className="px-3 pb-3"><PlanMeta plan={{ files: entry.files ?? "", risks: entry.risks ?? "" }} /></div>}</>}
    </div>;
  }
  if (entry.kind === "delegation") {
    const running = entry.status === "running" || entry.status === "queued";
    const duration = running ? now - entry.ts * 1000 : entry.duration_ms;
    const open = expanded ?? running;
    return <div className="activity-delegation"><button className="activity-entry-toggle" aria-expanded={open} onClick={() => setExpanded(!open)}><Bot size={16} className="text-accent" /><span className="min-w-0 flex-1 truncate">{entry.agent || t("activity.kinds.delegation")}</span><span className="text-xs text-muted">{t(`activity.status.${entry.status}`, { defaultValue: entry.status })}{duration != null && ` · ${durationLabel(Math.max(0, duration))}`}</span><ChevronDown size={14} className={cn(open && "rotate-180")} /></button>
      {open && <div className="activity-delegation-detail"><p><TaggedText content={entry.goal} /></p>{entry.run_id && <button className="activity-run-link" onClick={() => onReveal(entry.run_id!)}><ArrowUpRight size={13} />{t("activity.viewExecution")}</button>}{entry.result && <details><summary>{t("activity.result")}</summary><Markdown content={entry.result} /></details>}</div>}
    </div>;
  }
  const open = expanded ?? entry.status === "error";
  const duration = entry.status === "running" ? Math.max(0, now - entry.ts * 1000) : entry.duration_ms;
  return <div className={cn("activity-tool", entry.status === "error" && "is-error")}>
    <button className="activity-entry-toggle" aria-expanded={open} onClick={() => setExpanded(!open)}>
      {entry.status === "running" ? <Loader2 size={15} className="animate-spin text-accent" /> : entry.status === "done" ? <Check size={15} className="text-ok" /> : <AlertCircle size={15} className="text-warn" />}
      <span className="min-w-0 flex-1 truncate font-mono" title={entry.name}>{entry.name}</span>
      <span className="text-xs tabular-nums text-muted">{t(`activity.status.${entry.status}`)}{duration != null && ` · ${durationLabel(duration)}`}</span>
      <ChevronDown size={14} className={cn("text-muted transition-transform", open && "rotate-180")} />
    </button>
    <ActivityTargets targets={entry.targets} />
    {open && <div className="activity-tool-detail">{entry.truncated && <p className="activity-note">{t("activity.truncated")}</p>}{entry.arguments && <section aria-label={t("activity.arguments")}><h3>{t("activity.arguments")}</h3><pre>{readableJson(entry.arguments)}</pre></section>}{entry.result && <section aria-label={t("activity.result")}><h3>{t("activity.result")}</h3><ActivityToolResult result={entry.result} /></section>}<p className="activity-trace-id">{t("activity.callId")}: {entry.id}{entry.request_id && ` · ${t("activity.requestId")}: ${entry.request_id}`}</p></div>}
  </div>;
}
