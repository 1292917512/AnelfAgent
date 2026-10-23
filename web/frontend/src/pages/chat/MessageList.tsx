import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, Loader2 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { FoldChip } from "./render/FoldChip";
import { ActivityRow } from "./ActivityRow";
import { MessageRow } from "./MessageRow";
import { HistoryStatus } from "./HistoryStatus";
import type { ChatMessage, ConversationFold } from "@/lib/types";

interface ReadingPosition { key?: string; offset: number; top: number; atBottom: boolean }
const readingPositions = new Map<string, ReadingPosition>();

type TimelineEntry =
  | { kind: "message"; ts: number; key: string; data: ChatMessage }
  | { kind: "fold"; ts: number; key: string; data: { fold: ConversationFold; messages: ChatMessage[] } }
;

/** Web 消息与折叠历史，保留滚动锚点和未发送草稿。 */
export function MessageList() {
  const { t } = useTranslation("chat");
  const activeChatId = useChatStore((s) => s.activeChatId);
  // 细粒度 selector：只订阅本组件需要的字段，其他会话/字段变化不触发重渲染
  const messages = useChatStore((s) => s.buckets[s.activeChatId]?.messages);
  const historyLoaded = useChatStore((s) => s.buckets[s.activeChatId]?.historyLoaded ?? false);
  const hasMore = useChatStore((s) => s.buckets[s.activeChatId]?.hasMore ?? false);
  const loadingEarlier = useChatStore((s) => s.buckets[s.activeChatId]?.loadingEarlier ?? false);
  const sseConnected = useChatStore((s) => s.streamStatus === "connected");
  const expanded = useChatStore((s) => s.buckets[s.activeChatId]?.historyExpanded ?? false);
  const showLatest = useChatStore((s) => s.showLatest);
  const wasExpanded = useRef(expanded);
  const loadEarlier = useChatStore((s) => s.loadEarlier);
  const scrollRef = useRef<HTMLDivElement>(null);
  const initialLoad = useRef(true);
  // 滚动锚定：prepend 更早历史时保持视口位置不跳动
  const prevFirstKey = useRef<string | null>(null);
  const prevLastKey = useRef<string | null>(null);
  const prevScrollHeight = useRef(0);
  // 用户是否停留在底部附近（吸底判定：上翻阅读时新消息不打断）
  const nearBottomRef = useRef(true);
  const prependAnchor = useRef<{ key: string; offset: number } | null>(null);
  const restorePrepend = useCallback(() => {
    const element = scrollRef.current;
    const saved = prependAnchor.current;
    if (!element || !saved) return;
    const row = Array.from(element.querySelectorAll<HTMLElement>("[data-message-key]")).find((item) => item.dataset.messageKey === saved.key);
    if (row) element.scrollTop += row.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.offset;
  }, []);
  const loadPrevious = async () => {
    const element = scrollRef.current;
    if (element) {
      const top = element.getBoundingClientRect().top;
      const row = Array.from(element.querySelectorAll<HTMLElement>("[data-message-key]")).find((item) => item.getBoundingClientRect().bottom > top);
      prependAnchor.current = row?.dataset.messageKey ? { key: row.dataset.messageKey, offset: row.getBoundingClientRect().top - top } : null;
      nearBottomRef.current = false;
    }
    const saved = prependAnchor.current;
    await loadEarlier(activeChatId);
    requestAnimationFrame(() => {
      if (prependAnchor.current === saved) { restorePrepend(); prependAnchor.current = null; }
    });
  };

  useLayoutEffect(() => {
    initialLoad.current = true;
    prependAnchor.current = null;
    prevFirstKey.current = null;
    prevLastKey.current = null;
    const element = scrollRef.current;
    return () => {
      if (!element || initialLoad.current) return;
      const top = element.getBoundingClientRect().top;
      const row = Array.from(element.querySelectorAll<HTMLElement>("[data-message-key]"))
        .find((item) => item.getBoundingClientRect().bottom > top);
      readingPositions.delete(activeChatId);
      readingPositions.set(activeChatId, { key: row?.dataset.messageKey, offset: row ? row.getBoundingClientRect().top - top : 0,
        top: element.scrollTop, atBottom: nearBottomRef.current });
      if (readingPositions.size > 40) readingPositions.delete(readingPositions.keys().next().value!);
    };
  }, [activeChatId]);

  // 监听滚动维护 nearBottom 状态（被动监听，不影响滚动性能）
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      if (!nearBottomRef.current && !initialLoad.current) useChatStore.getState().holdHistory();
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const folds = useChatStore((s) => s.buckets[s.activeChatId]?.folds);
  // 折叠起点消息集合（这些消息不再提供「从此换向」入口，避免同位置重复折叠）
  const foldPivotIds = useMemo(() => new Set((folds ?? []).map((f) => f.from_msg_id)), [folds]);

  const timeline = useMemo<TimelineEntry[]>(() => {
    const entries: TimelineEntry[] = [];
    // 换向折叠：msg id → 所属折叠段（from 不含、to 含；无 id 的本地消息永不折叠）
    const coveredBy = new Map<number, ConversationFold>();
    for (const f of folds ?? []) {
      for (const m of messages ?? []) {
        if (m.id != null && m.id > f.from_msg_id && m.id <= f.to_msg_id) {
          coveredBy.set(m.id, f);
        }
      }
    }
    // 无 ts 的旧数据继承前一条的 ts，保持消息间相对顺序（sort 稳定，同键保序）
    let lastMsgTs = 0;
    let collecting: { fold: ConversationFold; messages: ChatMessage[] } | null = null;
    const flushFold = () => {
      if (!collecting) return;
      entries.push({
        kind: "fold",
        ts: collecting.messages[0]?.ts ?? lastMsgTs,
        key: `fold-${collecting.fold.id}`,
        data: collecting,
      });
      collecting = null;
    };
    for (const m of messages ?? []) {
      if (m.kind === "tool_summary" || (!m.content && !m.media_type && !m.extension)) continue;
      const ts = m.ts ?? lastMsgTs;
      lastMsgTs = ts;
      const fold = m.id != null ? coveredBy.get(m.id) : undefined;
      if (fold) {
        // 同一折叠段连续收拢；跨段（理论上不交叠）先结算上一段
        if (collecting && collecting.fold.id !== fold.id) flushFold();
        if (!collecting) collecting = { fold, messages: [] };
        collecting.messages.push(m);
        continue;
      }
      flushFold();
      entries.push({
        kind: "message",
        ts,
        key: `msg-${m.id ?? m.cid ?? ts}-${m.role}`,
        data: m,
      });
    }
    flushFold();
    entries.sort((a, b) => a.ts - b.ts);
    return entries;
  }, [messages, folds]);

  useLayoutEffect(() => {
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
      const position = readingPositions.get(activeChatId);
      const anchor = position?.key && Array.from(el.querySelectorAll<HTMLElement>("[data-message-key]")).find((row) => row.dataset.messageKey === position.key);
      el.scrollTop = !position || position.atBottom ? el.scrollHeight : anchor
        ? el.scrollTop + anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - position.offset : position.top;
      nearBottomRef.current = !position || position.atBottom;
      initialLoad.current = false;
    } else if (prependAnchor.current) {
      restorePrepend();
    } else if (wasExpanded.current && !expanded) {
      el.scrollTop = el.scrollHeight;
      nearBottomRef.current = true;
    } else if (prepended) {
      el.scrollTo({ top: el.scrollHeight - prevScrollHeight.current + el.scrollTop });
    } else {
      // 吸底策略：本人刚发的消息强制吸底；其余仅当用户停留在底部附近才跟随，
      // 上翻阅读历史时新消息更新不打断当前位置
      const lastMsg = list[list.length - 1];
      const sentByMe = lastMsg?.role === "user" && lastKey !== prevLastKey.current;
      if (sentByMe || nearBottomRef.current) {
        el.scrollTop = el.scrollHeight;
      }
    }
    wasExpanded.current = expanded;
    prevFirstKey.current = firstKey;
    prevLastKey.current = lastKey;
    prevScrollHeight.current = el.scrollHeight;
  }, [messages, historyLoaded, activeChatId, expanded, restorePrepend]);


  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} style={{ overflowAnchor: expanded ? "auto" : "none" }} className="message-list flex-1 overflow-y-auto overscroll-contain min-h-0">
        <HistoryStatus />
        {!sseConnected && (
          <div className="flex justify-center" data-testid="sse-reconnect-banner">
            <div className="inline-flex items-center gap-1.5 text-[11px] text-warn rounded-full bg-warn-subtle px-3 py-1">
              <Loader2 size={11} className="animate-spin" />
              {t("stream.connectionLost")}
            </div>
          </div>
        )}
        {hasMore && (
          <div className="flex justify-center">
            <button
              onClick={() => void loadPrevious()}
              disabled={loadingEarlier}
              className="inline-flex items-center gap-1.5 text-[11px] text-muted hover:text-foreground transition-colors disabled:opacity-50 rounded-full bg-elevated px-3 py-1"
            >
              {loadingEarlier && <Loader2 size={11} className="animate-spin" />}
              {t("loadEarlier")}
            </button>
          </div>
        )}
        {historyLoaded && timeline.length === 0 && (
          <div className="conversation-welcome">
            <span className="welcome-orbit" aria-hidden="true"><span /></span>
            <h3>{t("welcomeTitle")}</h3>
            <p>{t("startConversation")}</p>
            <div className="welcome-actions">
              <button type="button" onClick={() => useWorkbenchStore.getState().showSurface("files")}>{t("welcomeFiles")}</button>
              <button type="button" onClick={() => useWorkbenchStore.getState().setActiveTab("tasks")}>{t("welcomeTasks")}</button>
            </div>
          </div>
        )}
        {timeline.map((entry) => {
          if (entry.kind === "fold") {
            return <FoldChip key={entry.key} fold={entry.data.fold} messages={entry.data.messages} />;
          }
          return (
            <MessageRow
              key={entry.key}
              msg={entry.data}
              foldPivot={entry.data.id != null && foldPivotIds.has(entry.data.id)}
            />
          );
        })}
        <ActivityRow />
      </div>
    {expanded && <button type="button" onClick={showLatest} className="absolute bottom-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-2 text-xs text-accent shadow-md"><ArrowDown size={13} />{t("showLatest")}</button>}
    </div>
  );
}
