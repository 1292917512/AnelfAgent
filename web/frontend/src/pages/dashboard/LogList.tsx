import { memo, useState, type ReactNode, type Ref } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Copy, ScrollText } from "lucide-react";
import type { LogEntry } from "@/lib/types";
import { toast } from "@/components/ui";
import { cn } from "@/lib/utils";

function Highlighted({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <>{text}</>;
  const lower = text.toLowerCase();
  const parts: ReactNode[] = [];
  let offset = 0;
  let match = lower.indexOf(keyword);
  while (match !== -1) {
    parts.push(text.slice(offset, match));
    parts.push(<mark key={match}>{text.slice(match, match + keyword.length)}</mark>);
    offset = match + keyword.length;
    match = lower.indexOf(keyword, offset);
  }
  parts.push(text.slice(offset));
  return <>{parts}</>;
}

const LogRow = memo(function LogRow({ entry, keyword, highlighted, rowRef, onTagClick }: {
  entry: LogEntry; keyword: string; highlighted: boolean; rowRef?: Ref<HTMLDivElement>; onTagClick: (tag: string) => void;
}) {
  const { t } = useTranslation("status");
  const [expanded, setExpanded] = useState(false);
  const long = entry.message.length > 420 || entry.message.split("\n").length > 4;
  const preview = long && !expanded && !keyword ? entry.message.split("\n").slice(0, 4).join("\n").slice(0, 420) : entry.message;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`[${entry.time}] ${entry.level} ${entry.tag ? `[${entry.tag}] ` : ""}${entry.message}`);
      toast.success(t("logsView.copied"));
    } catch { toast.error(t("logsView.copyFailed")); }
  };
  return <div ref={rowRef} className={cn("log-row", highlighted && "is-highlighted")} data-level={entry.level} data-seq={entry.seq}>
    <time title={entry.timestamp ? new Date(entry.timestamp * 1000).toLocaleString() : entry.time}>{entry.time}</time>
    <span className="log-level">{entry.level}</span>
    <span className="log-source">{entry.tag ? <button type="button" title={entry.tag} onClick={() => onTagClick(entry.tag)}>{entry.tag}</button> : "—"}</span>
    <div className="log-message"><span><Highlighted text={preview} keyword={keyword} /></span>
      {long && !keyword && <button type="button" className="log-expand" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><ChevronDown size={12} className={expanded ? "rotate-180" : ""} />{t(expanded ? "logsView.collapse" : "logsView.expand")}</button>}
    </div>
    <button type="button" className="log-copy" title={t("logsView.copy")} aria-label={t("logsView.copy")} onClick={() => void copy()}><Copy size={13} /></button>
  </div>;
});

export function LogList({ filtered, keyword, scrollRef, onScroll, highlightSeq, rowRef, loading, onTagClick }: {
  filtered: LogEntry[]; keyword: string; scrollRef: Ref<HTMLDivElement>; onScroll: () => void;
  highlightSeq: number | null; rowRef: Ref<HTMLDivElement>; loading: boolean; onTagClick: (tag: string) => void;
}) {
  const { t } = useTranslation("status");
  return <>
    <div className="logs-column-head" aria-hidden="true"><span>{t("logsView.time")}</span><span>{t("logsView.levels")}</span><span>{t("logsView.source")}</span><span>{t("logsView.message")}</span></div>
    <div ref={scrollRef} onScroll={onScroll} className="logs-scroll" role="region" aria-label={t("logsView.entries")} tabIndex={0}>
      {!filtered.length && <div className="logs-empty"><ScrollText size={28} /><p>{loading ? t("common:loading") : t("noMatchingLogs")}</p></div>}
      {filtered.map((entry) => <LogRow key={entry.seq} entry={entry} keyword={keyword} highlighted={entry.seq === highlightSeq} rowRef={entry.seq === highlightSeq ? rowRef : undefined} onTagClick={onTagClick} />)}
    </div>
  </>;
}
