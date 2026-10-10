import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { GitFork, Loader2, Mic, Copy, Volume2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatRelativeTimestamp } from "@/lib/format";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { ConfirmDialog } from "@/components/ui/Modal";
import { MediaBubble } from "./render/MediaBubble";
import { MessageExtension } from "@/components/extensions/MessageExtension";
import { SystemNotice } from "./render/SystemNotice";
import { CollapsibleUserMessage } from "./render/CollapsibleUserMessage";
import { CollapsibleMarkdown } from "./render/CollapsibleMarkdown";
import { FoldChip } from "./render/FoldChip";
import { ActivityRow } from "./ActivityRow";
import { HistoryStatus } from "./HistoryStatus";
import type { ChatMessage, ConversationFold } from "@/lib/types";

interface ReadingPosition { key?: string; offset: number; top: number; atBottom: boolean }
const readingPositions = new Map<string, ReadingPosition>();

type TimelineEntry =
  | { kind: "message"; ts: number; key: string; data: ChatMessage }
  | { kind: "fold"; ts: number; key: string; data: { fold: ConversationFold; messages: ChatMessage[] } }
;

/** 单条消息气泡（memo：流式 delta 更新时历史消息行不重渲染） */
const MessageRow = memo(function MessageRow({ msg, foldPivot }: { msg: ChatMessage; foldPivot?: boolean }) {
  const { t } = useTranslation("chat");
  const copyMessageToDraft = useChatStore((s) => s.copyMessageToDraft);
  const foldFrom = useChatStore((s) => s.foldFrom);
  const isUser = msg.role === "user";
  const [confirmFold, setConfirmFold] = useState(false);
  const [foldBusy, setFoldBusy] = useState(false);
  // 仅已落库的用户消息可作折叠起点。
  const canFold = isUser && msg.id != null && !foldPivot;

  const onFoldConfirm = async () => {
    if (msg.id == null) return;
    setFoldBusy(true);
    try {
      await foldFrom(msg.id);
    } finally {
      setFoldBusy(false);
      setConfirmFold(false);
    }
  };

  if (msg.role === "system" || msg.kind === "system_notice") {
    return <SystemNotice content={msg.content} tone={msg.tone} />;
  }

  return (
    <div data-message-key={`msg-${msg.id ?? msg.cid ?? msg.ts}-${msg.role}`} className={cn("flex group/msg", isUser ? "justify-end" : "justify-start")}>
      <div className={cn("message-content min-w-0", isUser ? "is-user text-right" : "is-assistant text-left")}>
        {msg.media_type && <MediaBubble msg={msg} />}
        {!isUser && msg.extension && <MessageExtension extension={msg.extension} />}
        {msg.content && (
          <div
            className={cn(
              "message-bubble rounded-2xl px-4 py-3 text-sm leading-relaxed inline-block text-left",
              isUser ? "bg-accent-subtle" : "bg-secondary",
              msg.delivery === "submitting" && "opacity-70",
              msg.delivery === "failed" && "border border-danger/50",
            )}
          >
            {isUser && msg.delivery && <p className={cn("mb-1 text-xs", msg.delivery === "failed" ? "text-danger" : "text-muted")} role="status">{t(`delivery.${msg.delivery}`)}</p>}
            {msg.voice && (
              <div className="flex items-center gap-1 text-xs text-muted mb-1">
                {msg.voice === "transcript"
                  ? <><Mic size={10} />{t("voice.transcript")}</>
                  : <><Volume2 size={10} />{t("voice.spoken")}</>}
              </div>
            )}
            {isUser ? (
              <CollapsibleUserMessage>
                <CollapsibleMarkdown
                  content={msg.content}
                  fadeClass="from-elevated"
                />
              </CollapsibleUserMessage>
            ) : (
              <CollapsibleMarkdown
                content={msg.content}
                fadeClass="from-card"
              />
            )}
          </div>
        )}
        {(msg.timestamp || msg.ts) && (
          <div
            className={cn(
              "text-[11px] text-muted mt-0.5 px-1 transition-opacity flex items-center gap-1.5",
              isUser ? "justify-end" : "justify-start",
            )}
            title={msg.timestamp}
          >
            {isUser && msg.cid && <button type="button" onClick={() => copyMessageToDraft(msg.cid!)}
              className="text-muted hover:text-accent" title={t("copyToDraft")} aria-label={t("copyToDraft")}><Copy size={12} /></button>}
            {canFold && (
              <button
                type="button"
                onClick={() => setConfirmFold(true)}
                className="md:opacity-0 group-hover/msg:opacity-100 focus-visible:opacity-100 transition-opacity text-muted hover:text-accent"
                title={t("fold.fromHere")}
              >
                <GitFork size={11} />
              </button>
            )}
            <span>{msg.ts ? formatRelativeTimestamp(msg.ts) : msg.timestamp}</span>
          </div>
        )}
      </div>
      <ConfirmDialog
        open={confirmFold}
        onClose={() => setConfirmFold(false)}
        onConfirm={() => void onFoldConfirm()}
        title={t("fold.confirmTitle")}
        message={t("fold.confirmMessage")}
        confirmText={t("fold.confirm")}
        loading={foldBusy}
      />
    </div>
  );
});

/** Web 消息与折叠历史，保留滚动锚点和未发送草稿。 */
export function MessageList() {
  const { t } = useTranslation("chat");
  const activeChatId = useChatStore((s) => s.activeChatId);
  // 细粒度 selector：只订阅本组件需要的字段，其他会话/字段变化不触发重渲染
  const messages = useChatStore((s) => s.buckets[s.activeChatId]?.messages);
  const sending = useChatStore((s) => s.buckets[s.activeChatId]?.sending ?? false);
  const historyLoaded = useChatStore((s) => s.buckets[s.activeChatId]?.historyLoaded ?? false);
  const hasMore = useChatStore((s) => s.buckets[s.activeChatId]?.hasMore ?? false);
  const loadingEarlier = useChatStore((s) => s.buckets[s.activeChatId]?.loadingEarlier ?? false);
  const sseConnected = useChatStore((s) => s.sseConnected);
  const loadEarlier = useChatStore((s) => s.loadEarlier);
  const scrollRef = useRef<HTMLDivElement>(null);
  const initialLoad = useRef(true);
  // 滚动锚定：prepend 更早历史时保持视口位置不跳动
  const prevFirstKey = useRef<string | null>(null);
  const prevLastKey = useRef<string | null>(null);
  const prevScrollHeight = useRef(0);
  // 用户是否停留在底部附近（吸底判定：上翻阅读时新消息不打断）
  const nearBottomRef = useRef(true);

  useLayoutEffect(() => {
    initialLoad.current = true;
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
      const position = readingPositions.get(activeChatId);
      const anchor = position?.key && Array.from(el.querySelectorAll<HTMLElement>("[data-message-key]")).find((row) => row.dataset.messageKey === position.key);
      el.scrollTop = !position || position.atBottom ? el.scrollHeight : anchor
        ? el.scrollTop + anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - position.offset : position.top;
      nearBottomRef.current = !position || position.atBottom;
      initialLoad.current = false;
    } else if (prepended) {
      el.scrollTo({ top: el.scrollHeight - prevScrollHeight.current + el.scrollTop });
    } else {
      // 吸底策略：本人刚发的消息强制吸底；其余仅当用户停留在底部附近才跟随，
      // 上翻阅读历史时新消息更新不打断当前位置
      const lastMsg = list[list.length - 1];
      const sentByMe = lastMsg?.role === "user" && lastKey !== prevLastKey.current;
      if (sentByMe || nearBottomRef.current) {
        el.scrollTo({ top: el.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      }
    }
    prevFirstKey.current = firstKey;
    prevLastKey.current = lastKey;
    prevScrollHeight.current = el.scrollHeight;
  }, [messages, historyLoaded, activeChatId]);


  return (
    <div ref={scrollRef} className="message-list flex-1 overflow-y-auto overscroll-contain min-h-0">
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
            onClick={() => void loadEarlier(activeChatId)}
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
      {sending && <ActivityRow />}
    </div>
  );
}
