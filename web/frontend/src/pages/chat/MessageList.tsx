import { memo, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { usePlanStore } from "@/stores/plan-store";
import { useDelegationStore } from "@/stores/delegation-store";
import { MediaBubble } from "./render/MediaBubble";
import { PlanCard } from "./render/PlanCard";
import { DelegationCard } from "./render/DelegationCard";
import { SystemNotice } from "./render/SystemNotice";
import { ToolSummaryCard } from "./render/ToolSummaryCard";
import { ToolCallsCard } from "./render/ToolCallsCard";
import { CollapsibleMarkdown } from "./render/CollapsibleMarkdown";
import { ActivityRow } from "./ActivityRow";
import { StreamingArea } from "./StreamingArea";
import type { ChatMessage, DelegationNode, PlanRecord } from "@/lib/types";

type TimelineEntry =
  | { kind: "message"; ts: number; key: string; data: ChatMessage }
  | { kind: "plan"; ts: number; key: string; data: PlanRecord }
  | { kind: "delegation"; ts: number; key: string; data: DelegationNode };

/** 单条消息气泡（memo：流式 delta 更新时历史消息行不重渲染） */
const MessageRow = memo(function MessageRow({ msg }: { msg: ChatMessage }) {
  const { t } = useTranslation("chat");
  const isUser = msg.role === "user";

  // 结构化消息：工具执行摘要卡片 / 系统提示细条（居中，不占气泡位）。
  // kind 由后端历史清洗返回；缺失时按内容前缀兜底分类，
  // 兼容未标记路径，避免执行记录落入普通气泡/系统细条
  const head = msg.content.trimStart();
  if (msg.kind === "tool_summary" || (!isUser && head.startsWith("[已执行操作摘要]"))) {
    return <ToolSummaryCard content={msg.content} />;
  }
  if (msg.role === "system" || msg.kind === "system_notice" || head.startsWith("[系统]") || head.startsWith("[执行步骤]")) {
    return <SystemNotice content={msg.content} tone={msg.tone} />;
  }

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[85%] sm:max-w-[80%]", isUser ? "text-right" : "text-left")}>
        {/* 本轮工具调用记录（固化卡片，默认折叠） */}
        {!isUser && msg.toolCalls && msg.toolCalls.length > 0 && (
          <ToolCallsCard tools={msg.toolCalls} />
        )}
        {msg.media_type && <MediaBubble msg={msg} />}
        {msg.content && (
          <div
            className={cn(
              "rounded-lg px-4 py-2.5 text-sm leading-relaxed inline-block text-left",
              isUser ? "bg-accent-subtle" : "bg-secondary",
              msg.queued && "border border-dashed border-muted-foreground/50 opacity-70",
            )}
          >
            {msg.queued && (
              <div className="text-[10px] text-muted mb-1">{t("queued")}</div>
            )}
            <CollapsibleMarkdown
              content={msg.content}
              fadeClass={isUser ? "from-accent-subtle" : "from-secondary"}
            />
          </div>
        )}
        {msg.timestamp && (
          <div className="text-[11px] text-muted mt-0.5 px-1">{msg.timestamp}</div>
        )}
      </div>
    </div>
  );
});

/** 消息列表：气泡渲染 + 自动滚动；按时间序合并 plan / delegation 卡片 */
export function MessageList() {
  const { t } = useTranslation("chat");
  const activeChatId = useChatStore((s) => s.activeChatId);
  // 细粒度 selector：只订阅本组件需要的字段，其他会话/字段变化不触发重渲染
  const messages = useChatStore((s) => s.buckets[s.activeChatId]?.messages);
  const sending = useChatStore((s) => s.buckets[s.activeChatId]?.sending ?? false);
  const historyLoaded = useChatStore((s) => s.buckets[s.activeChatId]?.historyLoaded ?? false);
  const hasMore = useChatStore((s) => s.buckets[s.activeChatId]?.hasMore ?? false);
  const loadingEarlier = useChatStore((s) => s.buckets[s.activeChatId]?.loadingEarlier ?? false);
  const loadEarlier = useChatStore((s) => s.loadEarlier);
  const scrollRef = useRef<HTMLDivElement>(null);
  const initialLoad = useRef(true);
  // 滚动锚定：prepend 更早历史时保持视口位置不跳动
  const prevFirstKey = useRef<string | null>(null);
  const prevLastKey = useRef<string | null>(null);
  const prevScrollHeight = useRef(0);

  const chatPlans = usePlanStore((s) => s.plans[activeChatId]);
  // 悬浮窗可见时计划由浮窗唯一展示，聊天流不重复渲染；关闭浮窗后回落到聊天流
  const panelHidden = usePlanStore((s) => s.panelHidden);
  const chatDelegations = useDelegationStore((s) => s.delegations[activeChatId]);

  // 把 messages / plans / delegations 按时间序合并到同一条时间线（输入不变时复用结果）
  const timeline = useMemo<TimelineEntry[]>(() => {
    const entries: TimelineEntry[] = [];
    // 本地新消息只有 cid 没有 DB id，继承前一条的 ts 保持追加在后（sort 稳定，同键保序）
    let lastMsgTs = 0;
    for (const m of messages ?? []) {
      const ts = m.id ?? lastMsgTs;
      lastMsgTs = ts;
      entries.push({
        kind: "message",
        ts,
        key: `msg-${m.id ?? m.cid ?? ts}-${m.role}`,
        data: m,
      });
    }
    if (panelHidden) {
      for (const p of Object.values(chatPlans ?? {})) {
        entries.push({
          kind: "plan",
          ts: p.created_at,
          key: `plan-${p.plan_id}`,
          data: p,
        });
      }
    }
    for (const d of Object.values(chatDelegations ?? {})) {
      entries.push({
        kind: "delegation",
        ts: d.started_at,
        key: `delegation-${d.delegation_id}`,
        data: d,
      });
    }
    entries.sort((a, b) => a.ts - b.ts);
    return entries;
  }, [messages, chatPlans, panelHidden, chatDelegations]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !historyLoaded) return;
    const list = messages ?? [];
    const first = list[0];
    const last = list[list.length - 1];
    const firstKey = first ? String(first.id ?? first.cid ?? "") : null;
    const lastKey = last ? String(last.id ?? last.cid ?? "") : null;
    // prepend 检测：首条变化且末条不变 → "加载更早"完成，恢复滚动锚点
    const prepended =
      prevFirstKey.current !== null &&
      firstKey !== prevFirstKey.current &&
      lastKey === prevLastKey.current;
    if (initialLoad.current) {
      el.scrollTo({ top: el.scrollHeight });
      initialLoad.current = false;
    } else if (prepended) {
      el.scrollTo({ top: el.scrollHeight - prevScrollHeight.current + el.scrollTop });
    } else {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
    prevFirstKey.current = firstKey;
    prevLastKey.current = lastKey;
    prevScrollHeight.current = el.scrollHeight;
  }, [messages, chatPlans, chatDelegations, historyLoaded]);

  // 切换会话时重置滚动初始位与锚点
  useEffect(() => {
    initialLoad.current = true;
    prevFirstKey.current = null;
    prevLastKey.current = null;
  }, [activeChatId]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pr-1 mb-3 min-h-0">
      {hasMore && (
        <div className="flex justify-center">
          <button
            onClick={() => void loadEarlier(activeChatId)}
            disabled={loadingEarlier}
            className="inline-flex items-center gap-1.5 text-[11px] text-muted hover:text-foreground transition-colors disabled:opacity-50 rounded-full bg-muted/50 px-3 py-1"
          >
            {loadingEarlier && <Loader2 size={11} className="animate-spin" />}
            {t("loadEarlier")}
          </button>
        </div>
      )}
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
        return <MessageRow key={entry.key} msg={entry.data} />;
      })}
      <StreamingArea />
      {sending && <ActivityRow />}
    </div>
  );
}
