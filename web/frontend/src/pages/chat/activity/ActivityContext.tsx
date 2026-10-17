import { BookOpen, ChevronDown, Loader2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ActivityEntry } from "@/lib/types/activity";
import { useNow } from "@/hooks/useNow";
import { formatElapsedCompact } from "@/lib/format";
import { cn } from "@/lib/utils";
import { TaggedText } from "./ActivityReferences";
import { SkillEvidence } from "@/components/common/SkillEvidence";

type ContextEntry = Extract<ActivityEntry, { kind: "context" }>;

function ContextBlock({ block }: { block: ContextEntry["blocks"][number] }) {
  const { t } = useTranslation("workbench");
  const [open, setOpen] = useState(false);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><ChevronDown size={13} aria-hidden="true" />{block.label}<span>{t("activity.contextChars", { count: block.content.length })}</span></summary>
    {open && <div><TaggedText content={block.content} /></div>}
  </details>;
}

/** 展示实际进入本轮模型上下文的记忆、关联与技能候选。 */
export function ActivityContext({ entry, defaultExpanded = false }: { entry: ContextEntry; defaultExpanded?: boolean }) {
  const { t } = useTranslation("workbench");
  const [open, setOpen] = useState(defaultExpanded);
  const running = entry.status === "running";
  const now = useNow(running);
  return <div className="activity-context">
    <button className="activity-entry-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      {running ? <Loader2 size={15} className="animate-spin text-accent" /> : <BookOpen size={15} className="text-accent" />}
      <span className="min-w-0 flex-1">{t(running ? "activity.contextPreparing" : "activity.contextReady")}</span>
      <span className="activity-entry-stat">{formatElapsedCompact(Math.max(0, running ? now - entry.ts * 1000 : entry.duration_ms))}</span>
      <ChevronDown size={14} className={cn("transition-transform", open && "rotate-180")} />
    </button>
    {!running && <div className="activity-context-overview">{Array.from(new Set(entry.blocks.map((block) => block.label))).map((label) => <span key={label}>{label}</span>)}</div>}
    {entry.error && <p className="activity-note text-warn" role="alert">{entry.error}</p>}
    {open && <div className="activity-context-detail">
      <p className="activity-note">{t("activity.contextHint")}</p>
      <SkillEvidence skills={entry.skills ?? []} />
      {entry.truncated && <p className="activity-note">{t("activity.truncated")}</p>}
      {entry.blocks.map((block, index) => <ContextBlock key={`${block.layer}:${index}`} block={block} />)}
      {!entry.blocks.length && !running && <p className="activity-note">{t("activity.contextEmpty")}</p>}
    </div>}
  </div>;
}
