/**
 * ChatTabs — 多会话横向切换 Tab。
 *
 * 顶部横向排列的 chat 列表：当前激活高亮、新建按钮、右键/长按删除（默认会话除外）。
 * 数据来自 chat-store.chats，切换 chat 触发 activeChatId 变更 → 消息流/PlanPanel 自动切换。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, MessageSquare, Pencil, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";

export function ChatTabs() {
  const { t } = useTranslation("chat");
  const chats = useChatStore((s) => s.chats);
  const activeChatId = useChatStore((s) => s.activeChatId);
  const buckets = useChatStore((s) => s.buckets);
  const setActiveChat = useChatStore((s) => s.setActiveChat);
  const newChat = useChatStore((s) => s.newChat);
  const removeChat = useChatStore((s) => s.removeChat);
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
      {chats.map((c) => {
        const isActive = c.chat_id === activeChatId;
        const isEditing = editingId === c.chat_id;
        const isDefault = c.chat_id === "default";
        const unread = buckets[c.chat_id]?.unread ?? 0;
        return (
          <div
            key={c.chat_id}
            className={cn(
              "group flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-md border text-xs whitespace-nowrap transition-colors shrink-0",
              isActive
                ? "bg-accent-subtle border-accent/50 text-foreground"
                : "bg-card border-border text-muted hover:text-foreground hover:border-border-strong",
            )}
          >
            <MessageSquare size={11} className={cn("shrink-0", isActive ? "text-accent" : "text-muted")} />
            {isEditing ? (
              <input
                autoFocus
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCommitEdit();
                  if (e.key === "Escape") handleCancelEdit();
                }}
                onBlur={handleCommitEdit}
                className="bg-transparent outline-none border-b border-accent w-24 text-xs"
              />
            ) : (
              <button
                onClick={() => setActiveChat(c.chat_id)}
                onDoubleClick={() => handleStartEdit(c.chat_id, c.title)}
                className="text-xs font-medium max-w-[120px] truncate"
                title={c.title}
              >
                {c.title}
              </button>
            )}
            {!isActive && unread > 0 && (
              <span
                className="flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-accent text-white text-[10px] font-semibold leading-none"
                title={t("tabs.unread", { count: unread, defaultValue: "{{count}} 条新消息" })}
              >
                {unread > 99 ? "99+" : unread}
              </span>
            )}
            {!isDefault && !isEditing && (
              <>
                <button
                  onClick={() => handleStartEdit(c.chat_id, c.title)}
                  className="p-0.5 opacity-0 group-hover:opacity-100 transition-opacity text-muted hover:text-foreground"
                  title={t("tabs.rename", { defaultValue: "重命名" })}
                >
                  <Pencil size={10} />
                </button>
                <button
                  onClick={() => removeChat(c.chat_id)}
                  className="p-0.5 opacity-0 group-hover:opacity-100 transition-opacity text-muted hover:text-red-500"
                  title={t("tabs.close", { defaultValue: "关闭" })}
                >
                  <X size={10} />
                </button>
              </>
            )}
            {isEditing && (
              <button
                onClick={handleCommitEdit}
                className="p-0.5 text-accent"
                title={t("tabs.confirm", { defaultValue: "确认" })}
              >
                <Check size={10} />
              </button>
            )}
          </div>
        );
      })}
      <button
        onClick={() => newChat()}
        className="flex items-center gap-1 px-2.5 py-1 rounded-md border border-dashed border-border text-xs text-muted hover:text-foreground hover:border-accent transition-colors shrink-0"
        title={t("tabs.new", { defaultValue: "新建会话" })}
      >
        <Plus size={11} />
        <span>{t("tabs.new", { defaultValue: "新建" })}</span>
      </button>
    </div>
  );
}
