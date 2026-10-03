/** 思考块 — 流式推理过程的可折叠展示。
 *
 * 生命周期严格限定在流式区：turn 结束即消失，不落对话历史、不进 LLM
 * 上下文；全量思考由思维链（thinking tracer）在内存会话中保存（开启时），
 * 展开态页脚提供跳转入口。思考中默认展开（活体感），正文到达后自动折叠
 * 为一行摘要（参照 ZCode「思考 · 持续了几秒」的收敛形态）。
 */

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Brain, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { Shimmer } from "@/components/common/Shimmer";
import { useThinkingStore } from "@/stores/thinking-store";
import { useWorkbenchStore } from "@/stores/workbench-store";

export function ThinkingBlock({
  reasoning,
  /** 思考是否仍在流式增长（正文到达后置 false，自动折叠） */
  active,
}: {
  reasoning: string;
  active: boolean;
}) {
  const { t } = useTranslation("chat");
  // 初始开合跟随 active：流式思考中展开，历史/正文到达后一行摘要
  const [open, setOpen] = useState(active);
  const bodyRef = useRef<HTMLDivElement>(null);
  const traceEnabled = useThinkingStore((s) => s.enabled);
  const setActiveTab = useWorkbenchStore((s) => s.setActiveTab);

  // 正文到达 = 思考结束，自动折叠为一行摘要（用户可手动再展开）
  useEffect(() => {
    if (!active) setOpen(false);
  }, [active]);

  // 流式期间跟随最新增量滚动到底（仅展开时）
  useEffect(() => {
    const el = bodyRef.current;
    if (open && active && el) el.scrollTop = el.scrollHeight;
  }, [reasoning, open, active]);

  return (
    <div className="rounded border border-border/60 bg-muted/40 text-xs overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted" />
        )}
        <Brain className="h-3.5 w-3.5 shrink-0 text-primary" />
        {active ? (
          <Shimmer className="text-muted">{t("stream.thinking")}</Shimmer>
        ) : (
          <span className="text-muted">{t("stream.thinkingDone")}</span>
        )}
      </button>
      {open && (
        <>
          <div
            ref={bodyRef}
            className="max-h-64 overflow-y-auto border-t border-border/40 px-3 py-2 whitespace-pre-wrap break-words text-muted leading-relaxed"
          >
            {reasoning}
          </div>
          {!active && traceEnabled && (
            <button
              type="button"
              onClick={() => setActiveTab("trace")}
              className="flex w-full items-center gap-1.5 border-t border-border/40 px-3 py-1.5 text-[10px] text-accent hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              {t("stream.viewInTrace")}
            </button>
          )}
        </>
      )}
    </div>
  );
}
