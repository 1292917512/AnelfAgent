import { chatApi } from "@/lib/api";
import { replaceEqualDeep } from "@tanstack/react-query";
import type { ChatBucket, ChatHistoryMessage, ChatMessage } from "@/lib/types";
import { DEFAULT_CHAT_ID } from "./chat-shared";

export const HISTORY_PAGE_SIZE = 40;
export const RECENT_MESSAGE_LIMIT = HISTORY_PAGE_SIZE * 3;
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
  const pendingText = local.filter((message) => message.id == null && !message.media_type && !message.extension);
  const messages: ChatMessage[] = incoming.map((message) => {
    const existing = (message.id != null ? byId.get(message.id) : undefined) ?? (message.cid ? byClient.get(message.cid) : undefined)
      ?? pendingText.find((candidate) => !matched.has(candidate) && candidate.role === message.role && candidate.content === message.content
        && (!candidate.cid || candidate.role !== "user"));
    if (!existing) return message;
    matched.add(existing);
    const merged: ChatMessage = { ...existing, ...message, content: existing.content };
    if (existing.delivery === "submitting" || existing.delivery === "unknown") merged.delivery = "submitted";
    return replaceEqualDeep(existing, merged);
  });
  messages.push(...local.filter((message) => !matched.has(message)));
  return messages.sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
}

/** Release only persisted history; unsent/uncertain messages and live attachments stay available. */
export function compactHistory(bucket: ChatBucket, limit = RECENT_MESSAGE_LIMIT): Partial<ChatBucket> {
  const persisted = bucket.messages.filter((message) => message.id != null);
  if (persisted.length <= limit) return {};
  const keep = new Set(persisted.slice(-limit));
  const messages = bucket.messages.filter((message) => message.id == null || keep.has(message));
  return { messages, earliestId: Math.min(...messages.flatMap((message) => message.id != null ? [message.id] : [])), hasMore: true };
}

/** Serializes history requests per conversation and preserves messages received during loading. */
export function createChatHistory(context: HistoryContext) {
  const requests = new Map<string, { refresh: boolean; promise: Promise<void>; controller: AbortController }>();
  async function load(chatId: string | undefined, mode: HistoryMode): Promise<void> {
    const id = chatId ?? context.activeId();
    const original = context.bucket(id);
    const inflight = requests.get(id);
    if (!original) return;
    if (inflight) {
      if (mode === "refresh") inflight.refresh = true;
      return inflight.promise;
    }
    if (mode === "initial" && original.historyLoaded) return;
    if (mode === "earlier" && (!original.hasMore || original.earliestId == null)) return;
    const ticket = { refresh: false, promise: Promise.resolve(), controller: new AbortController() };
    requests.set(id, ticket);
    context.update(id, () => mode === "earlier"
      ? { loadingEarlier: true, earlierError: null }
      : { historyLoading: true, historyError: null });
    ticket.promise = (async () => {
      try {
        let afterId = mode === "refresh" && original.historyLoaded
          ? original.latestId ?? Math.max(0, ...original.messages.map((message) => message.id ?? 0)) : undefined;
        while (true) {
          const { data } = await chatApi.history("web_user", HISTORY_PAGE_SIZE,
            id === DEFAULT_CHAT_ID ? undefined : id, mode === "earlier" ? original.earliestId : undefined, afterId, ticket.controller.signal);
          if (requests.get(id) !== ticket || !context.bucket(id)) return;
          const receivedId = Math.max(0, ...data.map((message) => message.id ?? 0));
          context.update(id, (bucket) => {
            const merged = mergeHistory(bucket.messages, data);
            const messages = merged.length === bucket.messages.length && merged.every((message, index) => message === bucket.messages[index]) ? bucket.messages : merged;
            const ids = messages.flatMap((message) => message.id != null ? [message.id] : []);
            return { messages, earliestId: ids.length ? Math.min(...ids) : undefined,
              latestId: Math.max(bucket.latestId ?? 0, receivedId), historyExpanded: mode === "earlier" || bucket.historyExpanded,
              historyLoaded: true,
              hasMore: mode === "refresh" && original.historyLoaded ? bucket.hasMore : data.length >= HISTORY_PAGE_SIZE };
          });
          if (afterId == null || data.length < HISTORY_PAGE_SIZE || receivedId <= afterId) break;
          afterId = receivedId;
        }
        if (mode !== "earlier" && !original.historyLoaded) context.onLoaded(id);
      } catch (error) {
        if (requests.get(id) === ticket && context.bucket(id)) context.update(id, () =>
          mode === "earlier" ? { earlierError: error } : { historyError: error });
      } finally {
        if (requests.get(id) === ticket) {
          requests.delete(id);
          if (context.bucket(id)) context.update(id, () => ({ historyLoading: false, loadingEarlier: false }));
          if (ticket.refresh) await load(id, "refresh");
        }
      }
    })();
    return ticket.promise;
  }
  return {
    load: (id?: string) => load(id, "initial"),
    earlier: (id?: string) => load(id, "earlier"),
    refresh: (id?: string) => load(id, "refresh"),
    cancel: (id?: string) => {
      const ids = id ? [id] : [...requests.keys()];
      for (const key of ids) {
        requests.get(key)?.controller.abort();
        requests.delete(key);
        if (context.bucket(key)) context.update(key, () => ({ historyLoading: false, loadingEarlier: false }));
      }
    },
  };
}
