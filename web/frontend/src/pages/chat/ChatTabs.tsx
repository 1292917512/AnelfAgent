/**
 * ChatTabs — 多会话横向切换 Tab。
 *
 * 顶部横向排列的 chat 列表：当前激活高亮、新建按钮、右键/长按删除（默认会话除外）。
 * 数据来自 chat-store.chats，切换 chat 触发 activeChatId 变更 → 消息流/PlanPanel 自动切换。
 *
 * 性能：每个 tab 的未读数由 ChatTabItem 独立订阅（细粒度 selector），
 * 任意会话的流式 delta 不会导致整个 tabs 条重渲染。
 */
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, MessageSquare, Pencil, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import type { ChatMeta } from "@/lib/types";
import type { TFunction } from "i18next";

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
  const isDefault = c.chat_id === "default";

  return (
    <div
      className={cn(
        "group flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-md border text-xs whitespace-nowrap transition-colors shrink-0",
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
          onDoubleClick={() => onStartEdit(c.chat_id, c.title)}
          className="text-xs font-medium max-w-[120px] truncate"
          title={c.title}
        >
          {c.title}
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
            onClick={() => onStartEdit(c.chat_id, c.title)}
            className="p-0.5 opacity-0 group-hover:opacity-100 transition-opacity text-muted hover:text-foreground"
            title={t("tabs.rename")}
            aria-label={t("tabs.rename")}
          >
            <Pencil size={10} />
          </button>
          <button
            onClick={() => removeChat(c.chat_id)}
            className="p-0.5 opacity-0 group-hover:opacity-100 transition-opacity text-muted hover:text-red-500"
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

  const handleStartEdit = (chatId: string, currentTitle: string) => {
    setEditingId(chatId);
    setEditTitle(currentTitle);
  };

  const handleCommitEdit = () => {
    if (editingId && editTitle.trim()) {
      renameChat(editingId, editTitle.trim());
    }
    setEditingId(null);
    setEditTitle("");
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditTitle("");
  };

  return (
    <div className="flex items-center gap-1.5 mb-2 -mt-1 overflow-x-auto no-scrollbar shrink-0">
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
      <button
        onClick={() => newChat()}
        className="flex items-center gap-1 px-2.5 py-1 rounded-md border border-dashed border-border text-xs text-muted hover:text-foreground hover:border-accent transition-colors shrink-0"
        title={t("tabs.new")}
        aria-label={t("tabs.new")}
      >
        <Plus size={11} />
        <span>{t("tabs.new")}</span>
      </button>
    </div>
  );
}
