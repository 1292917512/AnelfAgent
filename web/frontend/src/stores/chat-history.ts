import { chatApi } from "@/lib/api";
import type { ChatBucket, ChatHistoryMessage, ChatMessage } from "@/lib/types";
import { DEFAULT_CHAT_ID } from "./chat-shared";

export const HISTORY_PAGE_SIZE = 100;
type HistoryMode = "initial" | "earlier" | "refresh";
interface HistoryContext {
  activeId: () => string;
  bucket: (id: string) => ChatBucket | undefined;
  update: (id: string, patch: (bucket: ChatBucket) => Partial<ChatBucket>) => void;
  onLoaded: (id: string) => void;
}

function mergeHistory(local: ChatMessage[], incoming: ChatHistoryMessage[]): ChatMessage[] {
  const byId = new Map(local.filter((message) => message.id != null).map((message) => [message.id, message]));
  const byClient = new Map(local.filter((message) => message.cid).map((message) => [message.cid, message]));
  const matched = new Set<ChatMessage>();
  const messages: ChatMessage[] = incoming.map((message) => {
    const existing = (message.id != null ? byId.get(message.id) : undefined) ?? (message.cid ? byClient.get(message.cid) : undefined);
    if (!existing) return message;
    matched.add(existing);
    return { ...existing, ...message, content: existing.content, delivery: existing.delivery === "submitting" ? "submitted" : existing.delivery };
  });
  messages.push(...local.filter((message) => !matched.has(message)));
  return messages.sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
}

/** Serializes history requests per conversation and preserves messages received during loading. */
export function createChatHistory(context: HistoryContext) {
  const requests = new Map<string, object>();
  async function load(chatId: string | undefined, mode: HistoryMode): Promise<void> {
    const id = chatId ?? context.activeId();
    const original = context.bucket(id);
    if (!original || requests.has(id) || (mode === "initial" && original.historyLoaded)) return;
    if (mode === "earlier" && (!original.hasMore || original.earliestId == null)) return;
    const ticket = {};
    const snapshot = new Set(original.messages);
    requests.set(id, ticket);
    context.update(id, () => mode === "earlier"
      ? { loadingEarlier: true, earlierError: null }
      : { historyLoading: true, historyError: null });
    try {
      const { data } = await chatApi.history("web_user", HISTORY_PAGE_SIZE,
        id === DEFAULT_CHAT_ID ? undefined : id, mode === "earlier" ? original.earliestId : undefined);
      if (requests.get(id) !== ticket || !context.bucket(id)) return;
      context.update(id, (bucket) => {
        const first = data[0]?.id;
        const local = mode === "refresh" && first != null
          ? bucket.messages.filter((message) => message.id != null || message.role === "user" || message.kind === "system_notice" || !snapshot.has(message))
          : bucket.messages;
        const messages = mergeHistory(local, data);
        const ids = messages.flatMap((message) => message.id != null ? [message.id] : []);
        return { messages, earliestId: ids.length ? Math.min(...ids) : undefined,
          historyLoaded: true,
          hasMore: mode === "refresh" && original.historyLoaded ? bucket.hasMore : data.length >= HISTORY_PAGE_SIZE };
      });
      if (mode !== "earlier") context.onLoaded(id);
    } catch (error) {
      if (requests.get(id) === ticket && context.bucket(id)) context.update(id, () =>
        mode === "earlier" ? { earlierError: error } : { historyError: error });
    } finally {
      if (requests.get(id) === ticket) {
        requests.delete(id);
        if (context.bucket(id)) context.update(id, () => ({ historyLoading: false, loadingEarlier: false }));
      }
    }
  }
  return {
    load: (id?: string) => load(id, "initial"),
    earlier: (id?: string) => load(id, "earlier"),
    refresh: (id?: string) => load(id, "refresh"),
    cancel: (id?: string) => {
      const ids = id ? [id] : [...requests.keys()];
      for (const key of ids) {
        requests.delete(key);
        if (context.bucket(key)) context.update(key, () => ({ historyLoading: false, loadingEarlier: false }));
      }
    },
  };
}
