/**
 * Chat store 内部共享的常量与工具（chat-store / chat-sse-handlers / chat-upload 共用）。
 *
 * 独立成模块以避免 chat-sse-handlers / chat-upload 反向 import chat-store 造成循环依赖。
 */
import type { ChatBucket, ChatMeta } from "@/lib/types";

export const DEFAULT_CHAT_ID = "default";
const LOCAL_STORAGE_ACTIVE_KEY = "anelf:activeChatId";
const LOCAL_STORAGE_CHATS_KEY = "anelf:chats";

// crypto.randomUUID 仅在安全上下文(localhost/HTTPS)可用;局域网 HTTP 访问
// (http://192.168.x.x) 为非安全上下文,调用会抛 TypeError——必须带降级,
// 否则发送等链路在第一步就静默炸掉(异常发生在 updateBucket 之前,用户无感知)
export const nextCid = (): string =>
  globalThis.crypto?.randomUUID?.() ??
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}${Math.random().toString(36).slice(2, 6)}`;

export function emptyBucket(): ChatBucket {
  return {
    inputDraft: "",
    workspaceContextEnabled: true,
    submitting: false,
    messages: [],
    sending: false,
    sendingSince: null,
    pendingFiles: [],
    historyLoaded: false,
    unread: 0,
  };
}

export function loadActiveChatId(): string {
  try {
    return localStorage.getItem(LOCAL_STORAGE_ACTIVE_KEY) || DEFAULT_CHAT_ID;
  } catch {
    return DEFAULT_CHAT_ID;
  }
}

export function persistActiveChatId(chatId: string) {
  try {
    localStorage.setItem(LOCAL_STORAGE_ACTIVE_KEY, chatId);
  } catch { /* ignore */ }
}

export function loadChatsFromStorage(): ChatMeta[] {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_CHATS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as ChatMeta[];
  } catch {
    return [];
  }
}

export function persistChats(chats: ChatMeta[]) {
  try {
    localStorage.setItem(LOCAL_STORAGE_CHATS_KEY, JSON.stringify(chats));
  } catch { /* ignore */ }
}

export function genChatId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** 会话静默时核对历史与执行快照；经过时间不能证明执行已结束。 */
type WatchdogEntry = { timer: ReturnType<typeof setTimeout>; onTimeout: (chatId: string) => void };
const _watchdogs = new Map<string, WatchdogEntry>();
const SEND_RECONCILE_MS = 10_000;

/** 清理会话核对任务；省略 chatId 时清理全部任务。 */
export function clearSendWatchdog(chatId?: string) {
  if (chatId === undefined) {
    for (const entry of _watchdogs.values()) clearTimeout(entry.timer);
    _watchdogs.clear();
    return;
  }
  const entry = _watchdogs.get(chatId);
  if (entry) {
    clearTimeout(entry.timer);
    _watchdogs.delete(chatId);
  }
}

export function armSendWatchdog(chatId: string, onTimeout: (chatId: string) => void) {
  clearSendWatchdog(chatId);
  const timer = setTimeout(() => {
    _watchdogs.delete(chatId);
    onTimeout(chatId);
  }, SEND_RECONCILE_MS);
  _watchdogs.set(chatId, { timer, onTimeout });
}

/** 已收到本会话过程时推迟 HTTP 核对，持续流式输出不额外轮询。 */
export function touchSendWatchdog(chatId: string) {
  const entry = _watchdogs.get(chatId);
  if (!entry) return;
  clearTimeout(entry.timer);
  entry.timer = setTimeout(() => {
    _watchdogs.delete(chatId);
    entry.onTimeout(chatId);
  }, SEND_RECONCILE_MS);
}
