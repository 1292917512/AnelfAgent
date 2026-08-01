/**
 * ToolCallsCard — 固化在正式回复消息上的本轮工具调用记录（默认折叠）。
 *
 * 流式区的工具块在 reply 到达时固化到消息对象，回复完成后仍可回看；
 * 刷新后由 ToolSummaryCard（历史 [已执行操作摘要] 消息）接续同款体验。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ChatStreamingTool } from "@/lib/types";
import { ToolBlock, toolTitle } from "./ToolBlocks";

export function ToolCallsCard({ tools }: { tools: ChatStreamingTool[] }) {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(false);
  if (!tools.length) return null;

  const names = [...new Set(tools.map((tool) => toolTitle(tool.name, tool.arguments)))];
  const preview = names.slice(0, 3).join("、");
  const failed = tools.some((tool) => tool.status === "error");

  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 text-xs overflow-hidden mb-1.5">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left text-muted hover:text-foreground transition-colors"
      >
        <Wrench size={12} className="shrink-0" />
        <span className="shrink-0">{t("toolCalls", { count: tools.length })}</span>
        <span className="flex-1 min-w-0 truncate text-muted/80">
          {preview}{names.length > 3 ? " …" : ""}
        </span>
        {failed && <span className="shrink-0 text-danger">{t("toolCallsFailed")}</span>}
        {open ? <ChevronDown size={12} className="shrink-0" /> : <ChevronRight size={12} className="shrink-0" />}
      </button>
      {open && (
        <div className="border-t border-border/40 px-1.5 py-1.5 space-y-1 max-h-72 overflow-y-auto">
          {tools.map((tool) => (
            <ToolBlock key={tool.call_id} tool={tool} />
          ))}
        </div>
      )}
    </div>
  );
}
