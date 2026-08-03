import type { ReactNode, Ref } from "react";
import { useTranslation } from "react-i18next";
import type { LogEntry } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";

const LEVEL_BADGE: Record<string, string> = {
  DEBUG: "bg-secondary text-muted border-border",
  INFO: "bg-[rgba(59,130,246,0.12)] text-info border-[rgba(59,130,246,0.3)]",
  WARNING: "bg-warn-subtle text-warn border-[rgba(245,158,11,0.3)]",
  ERROR: "bg-danger-subtle text-danger border-[rgba(239,68,68,0.3)]",
  CRITICAL: "bg-danger-subtle text-danger border-[rgba(239,68,68,0.3)] font-bold",
};

/** 列表行：附加单调序号作为稳定 key（服务端日志条目无唯一 id） */
export type LogRow = LogEntry & { seq: number };

/** 关键词高亮渲染 */
function Highlighted({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <>{text}</>;
  const lower = text.toLowerCase();
  const kw = keyword.toLowerCase();
  const parts: ReactNode[] = [];
  let i = 0;
  let k = 0;
  for (;;) {
    const idx = lower.indexOf(kw, i);
    if (idx === -1) {
      parts.push(text.slice(i));
      break;
    }
    if (idx > i) parts.push(text.slice(i, idx));
    parts.push(
      <mark key={k++} className="bg-accent-subtle text-accent rounded-sm px-0.5">
        {text.slice(idx, idx + kw.length)}
      </mark>,
    );
    i = idx + kw.length;
  }
  return <>{parts}</>;
}

/** 日志滚动列表 */
export function LogList({
  filtered,
  keyword,
  scrollRef,
  onScroll,
  highlightSeq,
  rowRef,
}: {
  filtered: LogRow[];
  keyword: string;
  scrollRef: Ref<HTMLDivElement>;
  onScroll: () => void;
  /** 需要高亮定位的条目序号（无则不高亮） */
  highlightSeq?: number | null;
  /** 高亮行的 DOM 引用，供外部滚动定位 */
  rowRef?: Ref<HTMLDivElement>;
}) {
  const { t } = useTranslation("status");
  return (
    <Card className="!p-0 overflow-hidden">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="max-h-[560px] overflow-y-auto font-mono text-[11px] sm:text-[12px] py-1"
      >
        {filtered.length === 0 && (
          <p className="text-sm text-muted py-8 text-center font-sans">{t("noMatchingLogs")}</p>
        )}
        {filtered.map((entry) => (
          <div
            key={entry.seq}
            ref={entry.seq === highlightSeq ? rowRef : undefined}
            className={cn(
              "flex items-start gap-2 py-1 px-3 hover:bg-hover transition-colors",
              entry.seq === highlightSeq && "bg-accent-subtle",
            )}
          >
            <span className="text-muted flex-shrink-0 w-16">{entry.time}</span>
            <span
              className={cn(
                "flex-shrink-0 w-[68px] text-center px-1 py-px rounded border text-[10px] leading-4",
                LEVEL_BADGE[entry.level] ?? "bg-secondary text-foreground border-border",
              )}
            >
              {entry.level}
            </span>
            {entry.tag && (
              <span className="flex-shrink-0 px-1.5 py-px rounded text-[10px] leading-4 bg-secondary text-muted border border-border max-w-24 truncate">
                {entry.tag}
              </span>
            )}
            <span className="text-foreground break-all min-w-0">
              <Highlighted text={entry.message} keyword={keyword} />
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
