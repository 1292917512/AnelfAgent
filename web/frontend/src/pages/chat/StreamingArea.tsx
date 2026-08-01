/**
 * 流式过程区 — token 级渲染 + 内联工具块（过程性展示，不落对话历史）。
 *
 * 对齐 Claude Code 的设计：流式文本是消息数组的"尾随兄弟"，
 * 回复工具（send_message）落地时由正式气泡替换，过程内容不持久。
 * 工具块状态灯：running=脉冲，done=绿，error=红；连续只读工具可折叠。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { Markdown } from "./render/Markdown";
import { DiffView } from "./DiffView";
import { READONLY_TOOLS, ToolBlock } from "./render/ToolBlocks";

export function StreamingArea() {
  const { t } = useTranslation("chat");
  const streaming = useChatStore((s) => s.buckets[s.activeChatId]?.streaming ?? null);
  const [expanded, setExpanded] = useState(false);

  if (!streaming || (!streaming.text && !streaming.reasoning && streaming.tools.length === 0)) {
    return null;
  }

  const readonlyRuns = streaming.tools.filter((t) => READONLY_TOOLS.has(t.name));
  const otherTools = streaming.tools.filter((t) => !READONLY_TOOLS.has(t.name));
  const collapseReadonly = readonlyRuns.length >= 3 && !expanded;

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] sm:max-w-[80%] space-y-2">
        {/* 内联工具块（过程展示） */}
        {streaming.tools.length > 0 && (
          <div className="space-y-1">
            {collapseReadonly ? (
              <button
                onClick={() => setExpanded(true)}
                className="flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors"
              >
                <ChevronRight className="h-3.5 w-3.5" />
                {t("stream.readonlyCollapsed", { count: readonlyRuns.length })}
              </button>
            ) : (
              <>
                {readonlyRuns.length >= 3 && (
                  <button
                    onClick={() => setExpanded(false)}
                    className="flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors"
                  >
                    <ChevronDown className="h-3.5 w-3.5" />
                    {t("stream.collapse")}
                  </button>
                )}
                {readonlyRuns.map((tool) => (
                  <ToolBlock key={tool.call_id} tool={tool} />
                ))}
              </>
            )}
            {otherTools.map((tool) => (
              <ToolBlock key={tool.call_id} tool={tool} />
            ))}
          </div>
        )}

        {/* 文件编辑 diff（过程展示） */}
        {streaming.diffs.map((d, i) => (
          <DiffView key={`${d.path}-${i}`} path={d.path} diff={d.diff} additions={d.additions} removals={d.removals} />
        ))}

        {/* 流式文本气泡（尾随兄弟，正式回复到达时替换） */}
        {(streaming.text || streaming.reasoning) && (
          <div className="bg-secondary rounded-lg px-4 py-2.5 text-sm leading-relaxed">
            {streaming.reasoning && !streaming.text && (
              <div className="text-xs text-muted italic mb-1 whitespace-pre-wrap">
                {streaming.reasoning.slice(-300)}
              </div>
            )}
            {streaming.text && (
              <>
                <Markdown content={streaming.text} />
                <span className="inline-block w-1.5 h-4 bg-primary/70 animate-pulse-subtle align-text-bottom" />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
