/**
 * ToolSummaryCard — 历史消息中的「已执行操作摘要」折叠卡片。
 *
 * 后端 reply_finalize 把本轮工具执行记录以 system 消息入库，
 * 前端解析为 ZCode 风格的单行摘要（默认折叠，点击展开明细）。
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";

interface SummaryEntry {
  index: string;
  call: string;
  result: string;
}

/** 解析 "[已执行操作摘要] 本轮共执行 N 次工具\n  #1 name(args) → result" 格式 */
function parseSummary(content: string): { count: number; entries: SummaryEntry[] } {
  const lines = content.split("\n");
  const countMatch = /共执行\s*(\d+)\s*次/.exec(lines[0] ?? "");
  const entries: SummaryEntry[] = [];
  for (const line of lines.slice(1)) {
    const m = /^\s*#(\d+)\s+(.*?)\s*→\s*(.*)$/.exec(line);
    if (m) entries.push({ index: m[1] ?? "", call: m[2] ?? "", result: m[3] ?? "" });
    else if (line.trim()) entries.push({ index: "", call: line.trim(), result: "" });
  }
  return { count: countMatch ? Number(countMatch[1]) : entries.length, entries };
}

export function ToolSummaryCard({ content }: { content: string }) {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(false);
  const { count, entries } = useMemo(() => parseSummary(content), [content]);

  return (
    <div className="flex justify-start">
      <div className="max-w-[88%] sm:max-w-[80%] rounded-lg border border-border/60 bg-muted/30 text-xs overflow-hidden">
        <button
          onClick={() => setOpen(!open)}
          className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left text-muted hover:text-foreground transition-colors"
        >
          <Wrench size={12} className="shrink-0" />
          <span className="flex-1 min-w-0 truncate">
            {t("toolSummary", { count })}
          </span>
          {open ? <ChevronDown size={12} className="shrink-0" /> : <ChevronRight size={12} className="shrink-0" />}
        </button>
        {open && (
          <div className="border-t border-border/40 px-2.5 py-1.5 space-y-1 max-h-64 overflow-y-auto">
            {entries.map((e, i) => (
              <div key={i} className="font-mono text-[11px] leading-relaxed">
                <span className="text-foreground/80 break-all">{e.call}</span>
                {e.result && (
                  <div className="text-muted break-all whitespace-pre-wrap">→ {e.result}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
