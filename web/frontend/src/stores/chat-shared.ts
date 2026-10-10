/**
 * Chat store 内部共享的常量与工具（chat-store / chat-sse-handlers / chat-upload 共用）。
 *
 * 独立成模块以避免 chat-sse-handlers / chat-upload 反向 import chat-store 造成循环依赖。
 */
import type { ChatBucket, ChatMessage, ChatMeta } from "@/lib/types";

export const DEFAULT_CHAT_ID = "default";
const LOCAL_STORAGE_ACTIVE_KEY = "anelf:activeChatId";
const LOCAL_STORAGE_CHATS_KEY = "anelf:chats";

export const nextCid = () => crypto.randomUUID();

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

// ── 发送看门狗：120s 完全无输出（delta/tool_call/reply…）则复位发送态 ──
// 语义是停滞判定而非总时长上限：回合内任何活动都重置计时，持续有输出的
// 长回合不会触发；仅在彻底静默超过窗口时判定挂死。
// 按 chatId 独立计时：多会话并发发送互不覆盖（单例实现会让后发的会话
// 顶掉先发会话的兜底，其输入框永久停在"发送中"）。

type WatchdogEntry = { timer: ReturnType<typeof setTimeout>; onTimeout: (chatId: string) => void };
const _watchdogs = new Map<string, WatchdogEntry>();
const SEND_TIMEOUT_MS = 120_000;

/** 清理看门狗：传 chatId 清该会话，不传清全部（SSE 断开等全局收束场景） */
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
  }, SEND_TIMEOUT_MS);
  _watchdogs.set(chatId, { timer, onTimeout });
}

/** 回合内有输出活动（delta/tool_call/file_diff）时重置该会话的停滞计时 */
export function touchSendWatchdog(chatId: string) {
  const entry = _watchdogs.get(chatId);
  if (!entry) return;
  clearTimeout(entry.timer);
  entry.timer = setTimeout(() => {
    _watchdogs.delete(chatId);
    entry.onTimeout(chatId);
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
