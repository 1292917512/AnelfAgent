import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { usePlanStore } from "@/stores/plan-store";
import { useDelegationStore } from "@/stores/delegation-store";
import { Markdown } from "./render/Markdown";
import { MediaBubble } from "./render/MediaBubble";
import { PlanCard } from "./render/PlanCard";
import { DelegationCard } from "./render/DelegationCard";
import { ActivityRow } from "./ActivityRow";
import { StreamingArea } from "./StreamingArea";
import type { PlanRecord, DelegationNode } from "@/lib/types";

type TimelineEntry =
  | { kind: "message"; ts: number; key: string; data: import("@/stores/chat-store").ChatMessage }
  | { kind: "plan"; ts: number; key: string; data: PlanRecord }
  | { kind: "delegation"; ts: number; key: string; data: DelegationNode };

/** 消息列表：气泡渲染 + 自动滚动；按时间序合并 plan / delegation 卡片 */
export function MessageList() {
  const { t } = useTranslation("chat");
  const activeChatId = useChatStore((s) => s.activeChatId);
  const bucket = useChatStore((s) => s.buckets[s.activeChatId]);
  const messages = bucket?.messages ?? [];
  const sending = bucket?.sending ?? false;
  const historyLoaded = bucket?.historyLoaded ?? false;
  const scrollRef = useRef<HTMLDivElement>(null);
  const initialLoad = useRef(true);

  const chatPlans = usePlanStore((s) => s.plans[activeChatId]);
  const chatDelegations = useDelegationStore((s) => s.delegations[activeChatId]);

  // 把 messages / plans / delegations 按时间序合并到同一条时间线
  const timeline: TimelineEntry[] = [];
  for (const m of messages) {
    const ts = m.id ?? 0;
    timeline.push({
      kind: "message",
      ts,
      key: `msg-${m.id ?? m.cid ?? ts}-${m.role}`,
      data: m,
    });
  }
  for (const p of Object.values(chatPlans ?? {})) {
    timeline.push({
      kind: "plan",
      ts: p.created_at,
      key: `plan-${p.plan_id}`,
      data: p,
    });
  }
  for (const d of Object.values(chatDelegations ?? {})) {
    timeline.push({
      kind: "delegation",
      ts: d.started_at,
      key: `delegation-${d.delegation_id}`,
      data: d,
    });
  }
  timeline.sort((a, b) => a.ts - b.ts);

  useEffect(() => {
    if (!historyLoaded) return;
    if (initialLoad.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      initialLoad.current = false;
      return;
    }
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, chatPlans, chatDelegations, historyLoaded]);

  // 切换会话时重置滚动初始位
  useEffect(() => {
    initialLoad.current = true;
  }, [activeChatId]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pr-1 mb-3 min-h-0">
      {timeline.length === 0 && (
        <div className="flex items-center justify-center h-full text-muted text-sm">
          {t("startConversation")}
        </div>
      )}
      {timeline.map((entry) => {
        if (entry.kind === "plan") {
          return <PlanCard key={entry.key} plan={entry.data} />;
        }
        if (entry.kind === "delegation") {
          return <DelegationCard key={entry.key} node={entry.data} />;
        }
        const msg = entry.data;
        return (
          <div
            key={entry.key}
            className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}
          >
            <div className={cn("max-w-[85%] sm:max-w-[80%]", msg.role === "user" ? "text-right" : "text-left")}>
              {msg.media_type && <MediaBubble msg={msg} />}
              {msg.content && (
                <div
                  className={cn(
                    "rounded-lg px-4 py-2.5 text-sm leading-relaxed inline-block text-left",
                    msg.role === "user"
                      ? "bg-accent-subtle"
                      : msg.role === "system"
                        ? "bg-danger-subtle text-danger"
                        : "bg-secondary",
                    msg.queued && "border border-dashed border-muted-foreground/50 opacity-70",
                  )}
                >
                  {msg.queued && (
                    <div className="text-[10px] text-muted mb-1">{t("queued", "排队中 · 将于当前回复后处理")}</div>
                  )}
                  <Markdown content={msg.content} />
                </div>
              )}
              {msg.timestamp && (
                <div className="text-[11px] text-muted mt-0.5 px-1">{msg.timestamp}</div>
              )}
            </div>
          </div>
        );
      })}
      <StreamingArea />
      {sending && <ActivityRow />}
    </div>
  );
}
