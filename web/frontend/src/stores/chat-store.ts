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
import { captureWorkspaceContext } from "@/lib/workspace-context";
import { usePlanStore } from "./plan-store";
import { useDelegationStore } from "./delegation-store";
import { useWorkbenchStore } from "./workbench-store";
import { chatApi, workspaceApi } from "@/lib/api";
import i18n from "@/i18n";
import type { ChatBucket, ChatMeta, ContextUsage, PendingFile } from "@/lib/types";
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
import { createChatHistory } from "./chat-history";
import { attachChatSseHandlers } from "./chat-sse-handlers";
import {
  classifyFile,
  filterAcceptedFiles,
  makePendingFile,
  uploadPendingFiles,
} from "./chat-upload";

// ── SSE 单例 ──────────────────────────────────────────────────
let _eventSource: EventSource | null = null;
/** 曾成功连上过：区分"初次连接"与"断线后重连"（重连需补拉错过的帧） */
let _wasConnected = false;

interface ChatState {
  buckets: Record<string, ChatBucket>;
  activeChatId: string;
  chats: ChatMeta[];
  contextUsage: ContextUsage | null;
  /** SSE 实时流连接状态（断线时聊天流顶部显示恢复横幅） */
  sseConnected: boolean;

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
  /** 断线重连后补拉当前会话最近一页（错过的落地帧经历史补齐） */
  refreshAfterReconnect: (chatId?: string) => Promise<void>;
  clearMessages: () => void;
  addFiles: (files: FileList | null) => Promise<void>;
  attachWorkspaceFile: (path: string, name: string, root?: "workspace" | "project") => void;
  attachWorkspaceDir: (path: string, name: string, root?: "workspace" | "project") => void;
  removeFile: (idx: number) => void;
  copyMessageToDraft: (cid: string) => void;
  setWorkspaceContextEnabled: (chatId: string, enabled: boolean) => void;
  setInputDraft: (chatId: string, text: string) => void;
  send: (text: string, userName: string) => Promise<boolean>;
  interrupt: () => Promise<void>;
  /** 加载该会话生效中的换向折叠段 */
  loadFolds: (chatId?: string) => Promise<void>;
  /** 从某条消息换向：其后消息折叠出上下文（返回是否成功） */
  foldFrom: (messageId: number) => Promise<boolean>;
  /** 恢复折叠段：消息重新进入上下文 */
  unfoldFold: (foldId: number) => Promise<boolean>;
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

  const history = createChatHistory({
    activeId: () => get().activeChatId,
    bucket: (id) => get().buckets[id],
    update: updateBucket,
    onLoaded: (id) => {
      void get().loadFolds(id);
      void chatApi.delegations(id === DEFAULT_CHAT_ID ? undefined : id).then(({ data }) => {
        if (get().buckets[id] && data.running?.length) useDelegationStore.getState().rehydrate(id, data.running);
      }).catch(() => {});
    },
  });

  return {
    buckets: initialBuckets,
    activeChatId: initialChatId,
    chats: initialChats,
    contextUsage: null,
    sseConnected: false,

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
      history.cancel(chatId);
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

    loadHistory: history.load,
    loadEarlier: history.earlier,

    interrupt: async () => {
      const chatId = get().activeChatId;
      try {
        const r = await chatApi.interrupt(chatId === DEFAULT_CHAT_ID ? undefined : chatId);
        // 无进行中的回复/子代理：本地直接复位发送态，避免空等 turn_end
        if (r.data?.status === "idle") {
          clearSendWatchdog(chatId);
          updateBucket(chatId, () => ({ sending: false, sendingSince: null, streaming: null }));
        }
      } catch { /* 中断失败时由看门狗兜底复位 */ }
    },

    loadFolds: async (chatId) => {
      const targetChatId = chatId ?? get().activeChatId;
      try {
        const r = await chatApi.folds(
          "web_user", targetChatId === DEFAULT_CHAT_ID ? undefined : targetChatId,
        );
        if (get().buckets[targetChatId]) updateBucket(targetChatId, () => ({ folds: r.data?.folds ?? [] }));
      } catch { /* ignore */ }
    },

    foldFrom: async (messageId) => {
      const targetChatId = get().activeChatId;
      try {
        await chatApi.fold(
          messageId, "web_user", targetChatId === DEFAULT_CHAT_ID ? undefined : targetChatId,
        );
        await get().loadFolds(targetChatId);
        return true;
      } catch {
        useWorkbenchStore.getState().pushNotification({
          id: nextCid(), title: "",
          content: i18n.t("fold.foldFailed", { ns: "chat" }),
          level: "warning", ts: Date.now() / 1000,
        });
        return false;
      }
    },

    unfoldFold: async (foldId) => {
      const targetChatId = get().activeChatId;
      try {
        await chatApi.unfold(foldId);
        await get().loadFolds(targetChatId);
        return true;
      } catch {
        useWorkbenchStore.getState().pushNotification({
          id: nextCid(), title: "",
          content: i18n.t("fold.restoreFailed", { ns: "chat" }),
          level: "warning", ts: Date.now() / 1000,
        });
        return false;
      }
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

      es.onopen = () => {
        set({ sseConnected: true });
        if (_wasConnected) {
          // 断线重连：补拉当前会话最近一页历史——断线窗口内落地的回复
          // 帧（delta/turn_end）不会重发，只有刷新历史才能补齐
          const chatId = get().activeChatId;
          void get().refreshAfterReconnect(chatId);
        }
        _wasConnected = true;
      };
      es.onerror = () => {
        set({ sseConnected: false });
        if (es.readyState === EventSource.CLOSED) _eventSource = null;
      };
    },

    refreshAfterReconnect: history.refresh,

    stopSSE: () => {
      history.cancel();
      clearSendWatchdog();
      _eventSource?.close();
      _eventSource = null;
      set({ sseConnected: false });
    },

    clearMessages: () => {
      const chatId = get().activeChatId;
      const bucket = get().buckets[chatId];
      history.cancel(chatId);
      if (bucket) revokeMessageBlobUrls(bucket.messages);
      updateBucket(chatId, () => ({ messages: [], historyLoaded: true, historyError: null, earlierError: null, hasMore: false, earliestId: undefined }));
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

    attachWorkspaceFile: (path, name, root = "workspace") => {
      const chatId = get().activeChatId;
      const type = classifyFile(name);
      const stub = new File([], name);
      updateBucket(chatId, (b) => ({
        pendingFiles: [...b.pendingFiles, {
          file: stub,
          type,
          uploading: false,
          path,
          root,
          preview: type === "image" ? workspaceApi.rawUrl(path, false, root) : undefined,
        }],
      }));
    },

    // 目录引用：不读内容，作为目录锚点附加（AI 经 list_directory 展开）
    attachWorkspaceDir: (path, name, root = "workspace") => {
      const chatId = get().activeChatId;
      const stub = new File([], name);
      updateBucket(chatId, (b) => ({
        pendingFiles: [...b.pendingFiles, {
          file: stub,
          type: "dir",
          uploading: false,
          path,
          root,
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

    copyMessageToDraft: (cid) => {
      const chatId = get().activeChatId;
      const target = get().buckets[chatId]?.messages.find((message) => message.cid === cid && message.role === "user");
      if (target) updateBucket(chatId, (bucket) => ({
        inputDraft: [bucket.inputDraft, target.content].filter(Boolean).join("\n\n"),
      }));
    },

    setWorkspaceContextEnabled: (chatId, workspaceContextEnabled) => {
      if (get().buckets[chatId]) updateBucket(chatId, () => ({ workspaceContextEnabled }));
    },

    setInputDraft: (chatId, inputDraft) => {
      if (get().buckets[chatId]) updateBucket(chatId, () => ({ inputDraft }));
    },

    send: async (text, userName) => {
      const chatId = get().activeChatId;
      const bucket = get().buckets[chatId] ?? emptyBucket();
      if (bucket.submitting) return false;
      const pendingFiles = bucket.pendingFiles;
      // 上传中/上传失败的附件必须拦下发送——静默丢弃会在消息里留下
      // [file: name] 占位但 AI 永远收不到文件，且泄漏 blob: 预览 URL
      if (pendingFiles.some((f) => f.uploading)) {
        useWorkbenchStore.getState().pushNotification({
          id: nextCid(), title: "",
          content: i18n.t("attachmentUploading", { ns: "chat" }),
          level: "warning", ts: Date.now() / 1000,
        });
        return false;
      }
      if (pendingFiles.some((f) => !f.path && !f.root)) {
        useWorkbenchStore.getState().pushNotification({
          id: nextCid(), title: "",
          content: i18n.t("attachmentFailed", { ns: "chat" }),
          level: "warning", ts: Date.now() / 1000,
        });
        return false;
      }
      const uploadedPaths = pendingFiles
        .filter((f) => f.path)
        .map((f) => (f.root === "project" ? `project:${f.path}` : f.path!));
      if (!text.trim() && !uploadedPaths.length) return false;

      const displayParts: string[] = [];
      if (text.trim()) displayParts.push(text.trim());
      for (const pf of pendingFiles) {
        if (pf.root) {
          // 工作区/项目引用：mention 链接形态（气泡经 MentionMarkdown 渲染为可点击 chip；
          // project 前缀解析所属根；目录带 dir: 标记供 chip 分流（聚焦树而非开编辑器）
          const rootPrefix = pf.root === "project" ? "project:" : "";
          const dirPrefix = pf.type === "dir" ? "dir:" : "";
          displayParts.push(`[${pf.file.name}](./${rootPrefix}${dirPrefix}${pf.path})`);
        } else if (pf.type === "image" && pf.preview) {
          displayParts.push(`![image](${pf.preview})`);
        } else {
          displayParts.push(`[${pf.type}: ${pf.file.name}]`);
        }
      }

      // 注意：拼入消息 content 的 blob: 预览 URL 不能在此 revoke（否则已发送消息
      // 图片必裂图）；其所有权移交消息，由 removeChat / clearMessages 统一回收。

      const messageId = nextCid();
      const submittedAt = Date.now();
      updateBucket(chatId, (b) => ({
        messages: [...b.messages, {
          role: "user",
          content: displayParts.join("\n"),
          cid: messageId,
          ts: Date.now() / 1000,
          delivery: "submitting",
        }],
        pendingFiles: [],
        submitting: true,
        sending: true,
        sendingSince: submittedAt,
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
            ...cur.messages,
            { role: "system", kind: "system_notice", tone: "warn", content: i18n.t("sendTimeout", { ns: "chat" }), cid: nextCid(), ts: Date.now() / 1000 },
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
          bucket.workspaceContextEnabled ? captureWorkspaceContext() : undefined,
          messageId,
        );
        if (get().buckets[chatId]) updateBucket(chatId, (current) => ({
          inputDraft: current.inputDraft === text ? "" : current.inputDraft,
          messages: current.messages.map((message) => message.cid === messageId && message.delivery === "submitting" ? { ...message, delivery: "submitted" } : message),
        }));
        return true;
      } catch {
        if (!bucket.sending) clearSendWatchdog(chatId);
        if (get().buckets[chatId]) updateBucket(chatId, (current) => ({
          pendingFiles: [...pendingFiles, ...current.pendingFiles],
          ...(!bucket.sending && current.sendingSince === submittedAt ? { sending: false, sendingSince: null } : {}),
          messages: [
            ...current.messages.filter((message) => message.cid !== messageId),
            { role: "system", kind: "system_notice", tone: "warn", content: i18n.t("sendFailed", { ns: "chat" }), cid: nextCid(), ts: Date.now() / 1000 },
          ],
        }));
        return false;
      } finally {
        if (get().buckets[chatId]) updateBucket(chatId, () => ({ submitting: false }));
      }
    },
  };
});
