/**
 * ChatTabs — 多会话横向切换 Tab。
 *
 * 顶部横向排列的 chat 列表：当前激活高亮、新建按钮、重命名、关闭与会话检索（默认会话不可关闭）。
 * 数据来自 chat-store.chats，切换 chat 触发 activeChatId 变更 → 消息流/执行记录 自动切换。
 *
 * 性能：每个 tab 的未读数由 ChatTabItem 独立订阅（细粒度 selector），
 * 任意会话的流式 delta 不会导致整个 tabs 条重渲染。
 */
import { Button, ConfirmDialog, Input } from "@/components/ui";
import * as Popover from "@radix-ui/react-popover";
import { PopoverContent } from "@/components/ui/PopoverContent";
import { TabStrip } from "@/components/common/TabStrip";
import { memo, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, List, MessageSquare, Pencil, Plus, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import type { ChatMeta } from "@/lib/types";
import type { TFunction } from "i18next";

/** 未命名会话在展示时翻译，避免冷启动或语言切换把翻译键写入数据。 */
function chatTitle(chat: ChatMeta, t: TFunction): string {
  return chat.title || t(chat.chat_id === "default" ? "defaultChat" : "newChat");
}

interface ChatTabItemProps {
  chat: ChatMeta;
  isActive: boolean;
  editing: boolean;
  editTitle: string;
  onEditTitleChange: (v: string) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onStartEdit: (chatId: string, currentTitle: string) => void;
  t: TFunction;
}

/** 单个会话 Tab（memo + 独立订阅本 tab 的 unread） */
const ChatTabItem = memo(function ChatTabItem({
  chat: c,
  isActive,
  editing,
  editTitle,
  onEditTitleChange,
  onCommitEdit,
  onCancelEdit,
  onStartEdit,
  t,
}: ChatTabItemProps) {
  const unread = useChatStore((s) => s.buckets[c.chat_id]?.unread ?? 0);
  const setActiveChat = useChatStore((s) => s.setActiveChat);
  const removeChat = useChatStore((s) => s.removeChat);
  const [confirmClose, setConfirmClose] = useState(false);
  const requestClose = () => {
    const bucket = useChatStore.getState().buckets[c.chat_id];
    if (bucket && (bucket.inputDraft || bucket.pendingFiles.length || bucket.submitting || bucket.sending)) setConfirmClose(true);
    else removeChat(c.chat_id);
  };
  const isDefault = c.chat_id === "default";
  const title = chatTitle(c, t);

  return (
    <>
    <div
      className={cn(
        "chat-tab group flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-md border text-xs whitespace-nowrap transition-colors shrink-0",
        isActive
          ? "bg-accent-subtle border-accent/50 text-foreground"
          : "bg-card border-border text-muted hover:text-foreground hover:border-border-strong",
      )}
    >
      <MessageSquare size={11} className={cn("shrink-0", isActive ? "text-accent" : "text-muted")} />
      {editing ? (
        <input
          autoFocus
          value={editTitle}
          onChange={(e) => onEditTitleChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onCommitEdit();
            if (e.key === "Escape") onCancelEdit();
          }}
          onBlur={onCommitEdit}
          className="bg-transparent outline-none border-b border-accent w-24 text-xs"
        />
      ) : (
        <button
          onClick={() => setActiveChat(c.chat_id)}
          onDoubleClick={() => onStartEdit(c.chat_id, title)}
          className="text-xs font-medium max-w-[160px] truncate"
          aria-pressed={isActive}
          title={title}
        >
          {title}
        </button>
      )}
      {!isActive && unread > 0 && (
        <span
          className="flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-accent text-white text-[10px] font-semibold leading-none"
          title={t("tabs.unread", { count: unread })}
        >
          {unread > 99 ? "99+" : unread}
        </span>
      )}
      {!isDefault && !editing && (
        <>
          <button
            onClick={() => onStartEdit(c.chat_id, title)}
            className="p-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 focus-visible:opacity-100 transition-opacity text-muted hover:text-foreground"
            title={t("tabs.rename")}
            aria-label={t("tabs.rename")}
          >
            <Pencil size={10} />
          </button>
          <button
            onClick={requestClose}
            className="p-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 focus-visible:opacity-100 transition-opacity text-muted hover:text-red-500"
            title={t("tabs.close")}
            aria-label={t("tabs.close")}
          >
            <X size={10} />
          </button>
        </>
      )}
      {editing && (
        <button
          onClick={onCommitEdit}
          className="p-0.5 text-accent"
          title={t("tabs.confirm")}
          aria-label={t("tabs.confirm")}
        >
          <Check size={10} />
        </button>
      )}
    </div>
    <ConfirmDialog open={confirmClose} title={t("tabs.close")} message={t("closeDraftWarning")}
      onClose={() => setConfirmClose(false)} onConfirm={() => removeChat(c.chat_id)} danger />
    </>
  );
});

export function ChatTabs() {
  const { t } = useTranslation("chat");
  const chats = useChatStore((s) => s.chats);
  const activeChatId = useChatStore((s) => s.activeChatId);
  const newChat = useChatStore((s) => s.newChat);
  const renameChat = useChatStore((s) => s.renameChat);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  const handleStartEdit = useCallback((chatId: string, currentTitle: string) => {
    setEditingId(chatId);
    setEditTitle(currentTitle);
  }, []);

  const handleCommitEdit = useCallback(() => {
    if (editingId && editTitle.trim()) {
      renameChat(editingId, editTitle.trim());
    }
    setEditingId(null);
    setEditTitle("");
  }, [editingId, editTitle, renameChat]);

  const handleCancelEdit = useCallback(() => {
    setEditingId(null);
    setEditTitle("");
  }, []);

  return (
    <div className="chat-tabs flex min-w-0 flex-1 items-center gap-1">
      <TabStrip role="group" label={t("tabs.sessions")} activeKey={activeChatId} className="flex-1" contentClassName="gap-1.5">
      {chats.map((c) => (
        <ChatTabItem
          key={c.chat_id}
          chat={c}
          isActive={c.chat_id === activeChatId}
          editing={editingId === c.chat_id}
          editTitle={editTitle}
          onEditTitleChange={setEditTitle}
          onCommitEdit={handleCommitEdit}
          onCancelEdit={handleCancelEdit}
          onStartEdit={handleStartEdit}
          t={t}
        />
      ))}
      </TabStrip>
      <ChatSessionPicker />
      <Button onClick={() => void newChat()} variant="ghost" size="icon" title={t("tabs.new")}><Plus size={16} /></Button>
    </div>
  );
}

function ChatSessionPicker() {
  const { t } = useTranslation("chat");
  const chats = useChatStore((state) => state.chats);
  const activeId = useChatStore((state) => state.activeChatId);
  const select = useChatStore((state) => state.setActiveChat);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const terms = search.trim().toLocaleLowerCase();
  const matches = chats.filter((chat) => chatTitle(chat, t).toLocaleLowerCase().includes(terms));
  return <Popover.Root open={open} onOpenChange={(value) => { setOpen(value); if (!value) setSearch(""); }}>
    <Popover.Trigger asChild><Button size="icon" variant="ghost" title={t("tabs.sessions")}><List size={16} /></Button></Popover.Trigger>
    <PopoverContent align="end" className="w-80 p-2" aria-label={t("tabs.sessions")}>
      <div className="relative mb-2"><Search size={14} className="pointer-events-none absolute left-3 top-3 text-muted" />
        <Input aria-label={t("tabs.search")} placeholder={t("tabs.search")} value={search} onChange={(event) => setSearch(event.target.value)} className="pl-9" />
      </div>
      <div className="max-h-[min(50dvh,360px)] overflow-y-auto">
        {matches.map((chat) => <button key={chat.chat_id} type="button" aria-pressed={chat.chat_id === activeId}
          className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-hover aria-pressed:bg-accent-subtle aria-pressed:text-accent"
          onClick={() => { void select(chat.chat_id); setOpen(false); setSearch(""); }}>
          <MessageSquare size={14} className="shrink-0" /><span className="min-w-0 flex-1 break-words">{chatTitle(chat, t)}</span>
          <SessionUnread chatId={chat.chat_id} />{chat.chat_id === activeId && <Check size={14} className="shrink-0" />}
        </button>)}
        {!matches.length && <p role="status" className="p-3 text-sm text-muted">{t("tabs.noMatches")}</p>}
      </div>
    </PopoverContent>
  </Popover.Root>;
}

function SessionUnread({ chatId }: { chatId: string }) {
  const { t } = useTranslation("chat");
  const count = useChatStore((state) => state.buckets[chatId]?.unread ?? 0);
  return count > 0 ? <span className="rounded-full bg-accent-subtle px-2 text-xs text-accent" aria-label={t("tabs.unread", { count })}>{count > 99 ? "99+" : count}</span> : null;
}
