/**
 * ToolSummaryCard — 历史消息中的「已执行操作摘要」折叠卡片。
 *
 * 结构化条目由后端历史清洗随 kind=tool_summary 消息附带（services.chat
 * clean_message_for_display），前端只负责渲染（默认折叠，点击展开明细）。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ToolSummaryData } from "@/lib/types";

export function ToolSummaryCard({ summary }: { summary: ToolSummaryData }) {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(false);

  return (
    <div className="flex justify-start">
      <div className="max-w-[88%] sm:max-w-[80%] rounded-lg border border-border/60 bg-muted/30 text-xs overflow-hidden">
        <button
          onClick={() => setOpen(!open)}
          className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left text-muted hover:text-foreground transition-colors"
        >
          <Wrench size={12} className="shrink-0" />
          <span className="flex-1 min-w-0 truncate">
            {t("toolSummary", { count: summary.count })}
          </span>
          {open ? <ChevronDown size={12} className="shrink-0" /> : <ChevronRight size={12} className="shrink-0" />}
        </button>
        {open && (
          <div className="border-t border-border/40 px-2.5 py-1.5 space-y-1 max-h-64 overflow-y-auto">
            {summary.entries.map((e, i) => (
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
