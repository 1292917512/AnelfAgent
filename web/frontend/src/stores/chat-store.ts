/**
 * Chat store — 按 chat_id 分桶的对话状态。
 *
 * 关键设计：
 * - buckets[chat_id] 持有该会话的 messages / streaming / pendingFiles 等全部状态
 * - activeChatId 控制当前激活的会话（Header tab / 新建 / 切换）
 * - SSE 单连接：所有事件带 chat_id，路由到对应 bucket（事件处理见 chat-sse-handlers.ts）
 * - plan / delegation 事件分流到 plan-store / delegation-store（按 chat_id 维度）
 * - 文件上传逻辑见 chat-upload.ts；共享常量/工具见 chat-shared.ts
 *
 * 兼容：默认 chat_id = "default"（即旧 scope=user_web_user），历史数据无缝衔接。
 */
import { create } from "zustand";
import { usePlanStore } from "./plan-store";
import { useDelegationStore } from "./delegation-store";
import { chatApi, workspaceApi } from "@/lib/api";
import i18n from "@/i18n";
import type { ChatBucket, ChatHistoryMessage, ChatMeta, ContextUsage, PendingFile } from "@/lib/types";
import {
  DEFAULT_CHAT_ID,
  armSendWatchdog,
  clearSendWatchdog,
  emptyBucket,
  genChatId,
  loadActiveChatId,
  loadChatsFromStorage,
  nextCid,
  persistActiveChatId,
  persistChats,
  revokeMessageBlobUrls,
} from "./chat-shared";
import { attachChatSseHandlers } from "./chat-sse-handlers";
import {
  classifyFile,
  filterAcceptedFiles,
  makePendingFile,
  uploadPendingFiles,
} from "./chat-upload";

// ── SSE 单例 ──────────────────────────────────────────────────
let _eventSource: EventSource | null = null;

/** 历史分页大小（首次加载与"加载更早"共用） */
const HISTORY_PAGE_SIZE = 100;

interface ChatState {
  buckets: Record<string, ChatBucket>;
  activeChatId: string;
  chats: ChatMeta[];
  contextUsage: ContextUsage | null;

  /** 派生 helper：当前激活 bucket（渲染时直接用） */
  active: () => ChatBucket;

  setActiveChat: (chatId: string) => void;
  newChat: (title?: string) => string;
  removeChat: (chatId: string) => void;
  renameChat: (chatId: string, title: string) => void;

  loadChats: () => Promise<void>;
  loadHistory: (chatId?: string) => Promise<void>;
  loadEarlier: (chatId?: string) => Promise<void>;
  startSSE: () => void;
  stopSSE: () => void;
  clearMessages: () => void;
  addFiles: (files: FileList | null) => Promise<void>;
  attachWorkspaceFile: (path: string, name: string) => void;
  removeFile: (idx: number) => void;
  send: (text: string, userName: string) => Promise<boolean>;
  interrupt: () => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => {
  const initialChatId = loadActiveChatId();
  const initialChats = loadChatsFromStorage();
  const initialBuckets: Record<string, ChatBucket> = {};
  if (initialChats.length === 0) {
    initialBuckets[DEFAULT_CHAT_ID] = emptyBucket();
    initialChats.push({
      chat_id: DEFAULT_CHAT_ID,
      title: i18n.t("defaultChat", { ns: "chat" }),
      last_ts: 0,
      message_count: 0,
    });
  }
  for (const c of initialChats) {
    initialBuckets[c.chat_id] = emptyBucket();
  }
  if (!initialBuckets[initialChatId]) {
    initialBuckets[initialChatId] = emptyBucket();
    initialChats.unshift({
      chat_id: initialChatId,
      title: i18n.t("defaultChat", { ns: "chat" }),
      last_ts: 0,
      message_count: 0,
    });
  }

  function updateBucket(chatId: string, fn: (b: ChatBucket) => Partial<ChatBucket>) {
    set((s) => {
      const bucket = s.buckets[chatId] ?? emptyBucket();
      return {
        buckets: { ...s.buckets, [chatId]: { ...bucket, ...fn(bucket) } },
      };
    });
  }

  return {
    buckets: initialBuckets,
    activeChatId: initialChatId,
    chats: initialChats,
    contextUsage: null,

    active: () => {
      const s = get();
      return s.buckets[s.activeChatId] ?? emptyBucket();
    },

    setActiveChat: (chatId) => {
      if (!get().buckets[chatId]) {
        updateBucket(chatId, () => ({}));
      }
      set({ activeChatId: chatId });
      persistActiveChatId(chatId);
      // 切换到该会话即视为已读
      updateBucket(chatId, (b) => (b.unread ? { unread: 0 } : {}));
      // 切换 chat 时按需加载历史
      const bucket = get().buckets[chatId];
      if (bucket && !bucket.historyLoaded) {
        void get().loadHistory(chatId);
      }
    },

    newChat: (title) => {
      const chatId = genChatId();
      const meta: ChatMeta = {
        chat_id: chatId,
        title: title || i18n.t("newChat", { ns: "chat" }),
        last_ts: Date.now() / 1000,
        message_count: 0,
      };
      set((s) => ({
        chats: [meta, ...s.chats],
        buckets: { ...s.buckets, [chatId]: emptyBucket() },
      }));
      persistChats(get().chats);
      get().setActiveChat(chatId);
      return chatId;
    },

    removeChat: (chatId) => {
      if (chatId === DEFAULT_CHAT_ID) return; // 默认会话不可删除
      // 回收该会话消息内容里遗留的 blob: 预览 URL（发送时所有权已移交消息）
      const bucket = get().buckets[chatId];
      if (bucket) {
        revokeMessageBlobUrls(bucket.messages);
        for (const pf of bucket.pendingFiles) {
          if (pf.preview?.startsWith("blob:")) URL.revokeObjectURL(pf.preview);
        }
      }
      set((s) => {
        const chats = s.chats.filter((c) => c.chat_id !== chatId);
        const buckets = { ...s.buckets };
        delete buckets[chatId];
        const next: Partial<ChatState> = { chats, buckets };
        if (s.activeChatId === chatId) {
          next.activeChatId = DEFAULT_CHAT_ID;
          persistActiveChatId(DEFAULT_CHAT_ID);
        }
        return next as ChatState;
      });
      persistChats(get().chats);
      usePlanStore.getState().clear(chatId);
      useDelegationStore.getState().clear(chatId);
    },

    renameChat: (chatId, title) => {
      set((s) => ({
        chats: s.chats.map((c) => (c.chat_id === chatId ? { ...c, title } : c)),
      }));
      persistChats(get().chats);
    },

    loadChats: async () => {
      try {
        const r = await chatApi.chats("web_user");
        const server = r.data?.chats ?? [];
        if (!server.length) return;
        // 与本地合并：服务端为准，本地无服务端有的新增
        set((s) => {
          const map = new Map<string, ChatMeta>();
          for (const c of server) {
            map.set(c.chat_id, {
              chat_id: c.chat_id,
              title: c.title || i18n.t("newChat", { ns: "chat" }),
              last_ts: c.last_ts,
              message_count: c.message_count,
            });
          }
          for (const c of s.chats) {
            if (!map.has(c.chat_id)) map.set(c.chat_id, c);
          }
          const chats = [...map.values()].sort((a, b) => b.last_ts - a.last_ts);
          const buckets = { ...s.buckets };
          for (const c of chats) {
            if (!buckets[c.chat_id]) buckets[c.chat_id] = emptyBucket();
          }
          return { chats, buckets };
        });
        persistChats(get().chats);
      } catch { /* ignore */ }
    },

    loadHistory: async (chatId) => {
      const targetChatId = chatId ?? get().activeChatId;
      const bucket = get().buckets[targetChatId];
      if (bucket?.historyLoaded) return;
      try {
        const scopeId = "web_user";
        const r = await chatApi.history(scopeId, HISTORY_PAGE_SIZE, targetChatId === DEFAULT_CHAT_ID ? undefined : targetChatId);
        if (r.data?.length) {
          const list = r.data as ChatHistoryMessage[];
          updateBucket(targetChatId, () => ({
            messages: list.map((m) => ({
              role: m.role,
              content: m.content,
              timestamp: m.timestamp,
              id: m.id,
              kind: m.kind,
            })),
            earliestId: list[0]?.id,
            hasMore: list.length >= HISTORY_PAGE_SIZE,
          }));
        }
      } catch { /* ignore */ }
      updateBucket(targetChatId, () => ({ historyLoaded: true }));
      // 恢复运行中的子代理卡片（刷新页面后 SSE 不会重发 started 事件）
      try {
        const r = await chatApi.delegations(
          targetChatId === DEFAULT_CHAT_ID ? undefined : targetChatId,
        );
        if (r.data?.running?.length) {
          useDelegationStore.getState().rehydrate(targetChatId, r.data.running);
        }
      } catch { /* ignore */ }
    },

    loadEarlier: async (chatId) => {
      const targetChatId = chatId ?? get().activeChatId;
      const bucket = get().buckets[targetChatId];
      if (!bucket || !bucket.hasMore || bucket.loadingEarlier || !bucket.earliestId) return;
      updateBucket(targetChatId, () => ({ loadingEarlier: true }));
      try {
        const r = await chatApi.history(
          "web_user", HISTORY_PAGE_SIZE,
          targetChatId === DEFAULT_CHAT_ID ? undefined : targetChatId,
          bucket.earliestId,
        );
        const list = (r.data ?? []) as ChatHistoryMessage[];
        updateBucket(targetChatId, (b) => ({
          messages: [
            ...list.map((m) => ({
              role: m.role,
              content: m.content,
              timestamp: m.timestamp,
              id: m.id,
              kind: m.kind,
            })),
            ...b.messages,
          ],
          earliestId: list[0]?.id ?? b.earliestId,
          hasMore: list.length >= HISTORY_PAGE_SIZE,
          loadingEarlier: false,
        }));
      } catch {
        updateBucket(targetChatId, () => ({ loadingEarlier: false }));
      }
    },

    interrupt: async () => {
      const chatId = get().activeChatId;
      try {
        const r = await chatApi.interrupt(chatId === DEFAULT_CHAT_ID ? undefined : chatId);
        // 无进行中的回复/子代理：本地直接复位发送态，避免空等 turn_end
        if (r.data?.status === "idle") {
          clearSendWatchdog();
          updateBucket(chatId, () => ({ sending: false, sendingSince: null, streaming: null }));
        }
      } catch { /* 中断失败时由看门狗兜底复位 */ }
    },

    startSSE: () => {
      if (_eventSource) return; // 幂等：重复调用不重建连接
      const es = new EventSource("/api/chat/stream");
      _eventSource = es;

      attachChatSseHandlers(es, {
        updateBucket,
        getActiveChatId: () => get().activeChatId,
        setContextUsage: (usage) => set({ contextUsage: usage }),
        forEachBucket: (fn) => {
          for (const cid of Object.keys(get().buckets)) fn(cid);
        },
      });

      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) _eventSource = null;
      };
    },

    stopSSE: () => {
      clearSendWatchdog();
      _eventSource?.close();
      _eventSource = null;
    },

    clearMessages: () => {
      const chatId = get().activeChatId;
      const bucket = get().buckets[chatId];
      if (bucket) revokeMessageBlobUrls(bucket.messages);
      updateBucket(chatId, () => ({ messages: [] }));
    },

    addFiles: async (files) => {
      if (!files) return;
      const chatId = get().activeChatId;
      const bucket = get().buckets[chatId] ?? emptyBucket();
      const accepted = filterAcceptedFiles(files, bucket.pendingFiles.length);
      if (!accepted.length) return;
      const newFiles: PendingFile[] = accepted.map(makePendingFile);
      updateBucket(chatId, (b) => ({ pendingFiles: [...b.pendingFiles, ...newFiles] }));
      await uploadPendingFiles(chatId, newFiles, { updateBucket });
    },

    attachWorkspaceFile: (path, name) => {
      const chatId = get().activeChatId;
      const type = classifyFile(name);
      const stub = new File([], name);
      updateBucket(chatId, (b) => ({
        pendingFiles: [...b.pendingFiles, {
          file: stub,
          type,
          uploading: false,
          path,
          preview: type === "image" ? workspaceApi.rawUrl(path) : undefined,
        }],
      }));
    },

    removeFile: (idx) => {
      const chatId = get().activeChatId;
      updateBucket(chatId, (b) => {
        const f = b.pendingFiles[idx];
        // 仅回收 blob: 预览（workspace 附件的 preview 是后端 URL，不可 revoke）
        if (f?.preview?.startsWith("blob:")) URL.revokeObjectURL(f.preview);
        return { pendingFiles: b.pendingFiles.filter((_, i) => i !== idx) };
      });
    },

    send: async (text, userName) => {
      const chatId = get().activeChatId;
      const bucket = get().buckets[chatId] ?? emptyBucket();
      const pendingFiles = bucket.pendingFiles;
      const uploadedPaths = pendingFiles.filter((f) => f.path).map((f) => f.path!);
      if (!text.trim() && !uploadedPaths.length) return false;

      const displayParts: string[] = [];
      if (text.trim()) displayParts.push(text.trim());
      for (const pf of pendingFiles) {
        if (pf.type === "image" && pf.preview) {
          displayParts.push(`![image](${pf.preview})`);
        } else {
          displayParts.push(`[${pf.type}: ${pf.file.name}]`);
        }
      }

      // 注意：拼入消息 content 的 blob: 预览 URL 不能在此 revoke（否则已发送消息
      // 图片必裂图）；其所有权移交消息，由 removeChat / clearMessages 统一回收。

      updateBucket(chatId, (b) => ({
        messages: [...b.messages, {
          role: "user",
          content: displayParts.join("\n"),
          cid: nextCid(),
          queued: b.sending || undefined,
        }],
        pendingFiles: [],
        sending: true,
        sendingSince: Date.now(),
      }));

      armSendWatchdog(chatId, (cid) => {
        const s = get();
        const b = s.buckets[cid];
        if (!b?.sending) return;
        updateBucket(cid, (cur) => ({
          sending: false,
          sendingSince: null,
          streaming: null,
          messages: [
            ...cur.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
            { role: "system", kind: "system_notice", tone: "warn", content: i18n.t("sendTimeout", { ns: "chat" }), cid: nextCid() },
          ],
        }));
      });

      try {
        await chatApi.send(
          text.trim() || " ",
          "web_user",
          userName,
          uploadedPaths.length ? uploadedPaths : undefined,
          chatId === DEFAULT_CHAT_ID ? undefined : chatId,
        );
        return true;
      } catch {
        clearSendWatchdog();
        updateBucket(chatId, (b) => ({
          sending: false,
          sendingSince: null,
          messages: [
            ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
            { role: "system", kind: "system_notice", tone: "warn", content: i18n.t("sendFailed", { ns: "chat" }), cid: nextCid() },
          ],
        }));
        return false;
      }
    },
  };
});
