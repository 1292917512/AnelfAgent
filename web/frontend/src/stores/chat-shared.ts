/**
 * Chat store 内部共享的常量与工具（chat-store / chat-sse-handlers / chat-upload 共用）。
 *
 * 独立成模块以避免 chat-sse-handlers / chat-upload 反向 import chat-store 造成循环依赖。
 */
import type { ChatBucket, ChatMessage, ChatMeta } from "@/lib/types";

export const DEFAULT_CHAT_ID = "default";
export const LOCAL_STORAGE_ACTIVE_KEY = "anelf:activeChatId";
export const LOCAL_STORAGE_CHATS_KEY = "anelf:chats";

let _cidSeq = 0;
export const nextCid = () => `c-${++_cidSeq}`;

export function emptyBucket(): ChatBucket {
  return {
    messages: [],
    sending: false,
    sendingSince: null,
    streaming: null,
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

// ── 发送看门狗：120s 无 reply/delta 则复位发送态 ──

let _sendWatchdog: ReturnType<typeof setTimeout> | null = null;
const SEND_TIMEOUT_MS = 120_000;

export function clearSendWatchdog() {
  if (_sendWatchdog) {
    clearTimeout(_sendWatchdog);
    _sendWatchdog = null;
  }
}

export function armSendWatchdog(chatId: string, onTimeout: (chatId: string) => void) {
  clearSendWatchdog();
  _sendWatchdog = setTimeout(() => {
    _sendWatchdog = null;
    onTimeout(chatId);
  }, SEND_TIMEOUT_MS);
}

// ── blob: URL 生命周期 ──

const BLOB_URL_RE = /!\[[^\]]*\]\((blob:[^)\s]+)\)/g;

/**
 * 回收消息内容里拼入的 blob: 预览 URL。
 * 发送时图片预览 URL 会被拼进消息 content（markdown 图片语法），这些 URL 的
 * 所有权随之移交给消息；会话删除/消息清空时必须在此统一 revoke，避免内存泄漏。
 */
export function revokeMessageBlobUrls(messages: ChatMessage[]) {
  for (const m of messages) {
    if (!m.content || !m.content.includes("blob:")) continue;
    for (const match of m.content.matchAll(BLOB_URL_RE)) {
      const url = match[1];
      if (url) URL.revokeObjectURL(url);
    }
  }
}
