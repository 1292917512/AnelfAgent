/**
 * Chat store — 按 chat_id 分桶的对话状态。
 *
 * 关键设计：
 * - buckets[chat_id] 持有该会话的 messages / pendingFiles 等全部状态
 * - activeChatId 控制当前激活的会话（Header tab / 新建 / 切换）
 * - SSE 单连接：Web 消息按 chat_id 路由，全局过程独立交给 activity-store
 * - plan / delegation 事件分流到 plan-store / delegation-store（按 chat_id 维度）
 * - 文件上传逻辑见 chat-upload.ts；共享常量/工具见 chat-shared.ts
 *
 */
import { create } from "zustand";
import axios from "axios";
import { activeChatRun, submissionFinished } from "@/lib/chat-activity";
import { EventStream } from "@/lib/event-stream";
import { useActivityStore } from "./activity-store";
import { captureWorkspaceContext } from "@/lib/workspace-context";
import { fileReferenceMarkdown } from "@/lib/file-reference";
import { usePlanStore } from "./plan-store";
import { useDelegationStore } from "./delegation-store";
import { useWorkbenchStore } from "./workbench-store";
import { toast } from "./toast-store";
import { apiErrorMessage } from "@/lib/api/client";
import { chatApi, workspaceApi } from "@/lib/api";
import i18n from "@/i18n";
import type { ChatBucket, ChatMeta, PendingFile, StreamStatus } from "@/lib/types";
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
} from "./chat-shared";
import { compactHistory, createChatHistory, HISTORY_PAGE_SIZE } from "./chat-history";
import { attachChatSseHandlers } from "./chat-sse-handlers";
import {
  classifyFile,
  filterAcceptedFiles,
  makePendingFile,
  uploadPendingFiles,
} from "./chat-upload";

interface ChatState {
  buckets: Record<string, ChatBucket>;
  activeChatId: string;
  chats: ChatMeta[];
  /** SSE 实时流连接状态（断线时聊天流顶部显示恢复横幅） */
  streamStatus: StreamStatus;
  refreshRealtime: () => void;

  /** 派生 helper：当前激活 bucket（渲染时直接用） */
  active: () => ChatBucket;

  setActiveChat: (chatId: string) => void;
  newChat: (title?: string) => string;
  removeChat: (chatId: string) => void;
  renameChat: (chatId: string, title: string) => void;

  loadChats: () => Promise<void>;
  loadHistory: (chatId?: string) => Promise<void>;
  loadEarlier: (chatId?: string) => Promise<void>;
  showLatest: () => void;
  holdHistory: () => void;
  startSSE: () => void;
  stopSSE: () => void;
  /** 断线重连后按游标逐批补齐当前会话的新增历史 */
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
  let recentChats = [initialChatId];
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
      const next = { ...bucket, ...fn(bucket) };
      if (next.messages !== bucket.messages && !next.historyExpanded) Object.assign(next, compactHistory(next));
      return {
        buckets: { ...s.buckets, [chatId]: next },
      };
    });
  }

  const detailRequests = new Map<string, Promise<void>>();
  function refreshDetails(id: string): Promise<void> {
    const pending = detailRequests.get(id);
    if (pending) return pending;
    const observed = useDelegationStore.getState().delegations[id] ?? {};
    const request = Promise.all([
      get().loadFolds(id),
      chatApi.delegations(id === DEFAULT_CHAT_ID ? undefined : id).then(({ data }) => {
        if (get().buckets[id]) useDelegationStore.getState().rehydrate(id, data.running ?? [], observed);
      }).catch(() => {}),
    ]).then(() => {}).finally(() => { detailRequests.delete(id); });
    detailRequests.set(id, request);
    return request;
  }

  const history = createChatHistory({
    activeId: () => get().activeChatId,
    bucket: (id) => get().buckets[id],
    update: updateBucket,
    onLoaded: (id) => { void refreshDetails(id); },
  });

  function settleSubmission(chatId: string): void {
    const bucket = get().buckets[chatId];
    const latest = [...(bucket?.messages ?? [])].reverse().find((message) => message.role === "user");
    if (bucket?.sending && submissionFinished(useActivityStore.getState().runs, chatId, latest)) {
      clearSendWatchdog(chatId);
      updateBucket(chatId, () => ({ sending: false, sendingSince: null }));
    }
  }

  async function syncConversation(chatId: string, recover = false): Promise<void> {
    const loaded = get().buckets[chatId]?.historyLoaded;
    await Promise.all([
      useActivityStore.getState().load(),
      history.refresh(chatId),
      recover && loaded ? refreshDetails(chatId) : Promise.resolve(),
    ]);
    settleSubmission(chatId);
  }

  function resync(): void {
    const state = get();
    const delegated = Object.entries(useDelegationStore.getState().delegations)
      .filter(([id, nodes]) => state.buckets[id] && Object.values(nodes).some((node) => node.status === "running")).map(([id]) => id);
    const ids = new Set([state.activeChatId, ...Object.keys(state.buckets).filter((id) => state.buckets[id]?.sending), ...delegated]);
    for (const id of ids) {
      if (state.buckets[id]?.sending) armSendWatchdog(id, checkPendingReply);
      void syncConversation(id, true);
    }
  }

  const stream = new EventStream("/api/chat/stream", (source, isCurrent, recover, received) => {
    return attachChatSseHandlers(source, {
      updateBucket, getActiveChatId: () => get().activeChatId,
      setContextUsage: (contextUsage, id) => updateBucket(id, () => ({ contextUsage })),
      onTurnEnd: (id) => { void syncConversation(id); },
    }, isCurrent, recover, received);
  }, (_connected, streamStatus) => set({ streamStatus }), () => resync(), resync,
    () => { void useActivityStore.getState().reconcile(); });

  async function checkPendingReply(chatId: string): Promise<void> {
    const pending = get().buckets[chatId];
    if (!pending?.sending) return;
    if (document.hidden) {
      if (stream.active) armSendWatchdog(chatId, checkPendingReply);
      return;
    }
    await Promise.all([useActivityStore.getState().reconcile(true), history.refresh(chatId)]);
    settleSubmission(chatId);
    const current = get().buckets[chatId];
    if (!stream.active || !current?.sending || current.sendingSince !== pending.sendingSince) return;
    armSendWatchdog(chatId, checkPendingReply);
  }

  return {
    buckets: initialBuckets,
    activeChatId: initialChatId,
    chats: initialChats,
    streamStatus: "stopped",
    refreshRealtime: () => stream.refresh(),

    active: () => {
      const s = get();
      return s.buckets[s.activeChatId] ?? emptyBucket();
    },

    setActiveChat: (chatId) => {
      const previousId = get().activeChatId;
      if (previousId !== chatId) {
        history.cancel(previousId);
        updateBucket(previousId, (bucket) => ({ ...compactHistory(bucket, HISTORY_PAGE_SIZE), historyExpanded: false }));
      }
      recentChats = [...new Set([chatId, previousId, ...recentChats])].slice(0, 3);
      for (const [id, bucket] of Object.entries(get().buckets)) {
        if (recentChats.includes(id) || bucket.sending || bucket.submitting || !bucket.historyLoaded) continue;
        history.cancel(id);
        updateBucket(id, (current) => ({ messages: current.messages.filter((message) => message.id == null),
          historyLoaded: false, historyExpanded: false, earliestId: undefined, latestId: undefined, hasMore: undefined,
          historyError: null, earlierError: null, folds: undefined }));
      }
      if (!get().buckets[chatId]) {
        updateBucket(chatId, () => ({}));
      }
      set({ activeChatId: chatId });
      persistActiveChatId(chatId);
      // 切换到该会话即视为已读
      updateBucket(chatId, (b) => (b.unread ? { unread: 0 } : {}));
      // 切换 chat 时按需加载历史
      const bucket = get().buckets[chatId];
      if (bucket) void (bucket.historyLoaded ? history.refresh(chatId) : history.load(chatId));
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
      const bucket = get().buckets[chatId];
      if (bucket) {
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
    holdHistory: () => {
      const id = get().activeChatId;
      if (!get().buckets[id]?.historyExpanded) updateBucket(id, () => ({ historyExpanded: true }));
    },
    showLatest: () => {
      const id = get().activeChatId;
      history.cancel(id);
      updateBucket(id, (bucket) => ({ ...compactHistory(bucket, HISTORY_PAGE_SIZE), historyExpanded: false }));
      void history.refresh(id);
    },

    interrupt: async () => {
      const chatId = get().activeChatId;
      const run = activeChatRun(useActivityStore.getState().runs, chatId);
      if (get().buckets[chatId]?.interruptPending || run?.control === "stopping") return;
      updateBucket(chatId, () => ({ interruptPending: true }));
      try {
        const r = run ? await workspaceApi.controlActivity(run.id, "stop")
          : await chatApi.interrupt(chatId === DEFAULT_CHAT_ID ? undefined : chatId);
        if (r.data.status === "error") throw new Error(r.data.error || i18n.t("activity.control.failed", { ns: "workbench" }));
        const failed = r.data.background?.filter((task) => !task.ok).length ?? 0;
        if (failed) toast.error(i18n.t("activity.control.backgroundPending", { ns: "workbench", count: failed }));
        // 无进行中的回复/子代理：本地直接复位发送态，避免空等 turn_end
        if (r.data?.status === "idle") {
          clearSendWatchdog(chatId);
          updateBucket(chatId, () => ({ sending: false, sendingSince: null }));
        }
      } catch (error) { toast.error(apiErrorMessage(error, i18n.t("activity.control.failed", { ns: "workbench" }))); }
      finally {
        await useActivityStore.getState().load();
        updateBucket(chatId, () => ({ interruptPending: false }));
      }
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

    startSSE: () => stream.start(),
    refreshAfterReconnect: history.refresh,
    stopSSE: () => {
      history.cancel();
      clearSendWatchdog();
      stream.stop();
    },

    clearMessages: () => {
      const chatId = get().activeChatId;
      history.cancel(chatId);
      updateBucket(chatId, () => ({ messages: [], historyLoaded: true, historyExpanded: false, historyError: null, earlierError: null, hasMore: false, earliestId: undefined }));
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
          displayParts.push(fileReferenceMarkdown({ path: pf.path ?? pf.file.name, root: pf.root, isDir: pf.type === "dir" }, pf.file.name));
        } else if (pf.type === "image" && pf.preview) {
          displayParts.push(`![image](${pf.preview})`);
        } else {
          displayParts.push(`[${pf.type}: ${pf.file.name}]`);
        }
      }

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

      armSendWatchdog(chatId, checkPendingReply);

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
        if (stream.active) void syncConversation(chatId);
        return true;
      } catch (error) {
        if (axios.isAxiosError(error) && !error.response && !axios.isCancel(error)) {
          if (get().buckets[chatId]) updateBucket(chatId, (current) => ({
            inputDraft: current.inputDraft === text ? "" : current.inputDraft,
            messages: current.messages.map((message) => message.cid === messageId ? { ...message, delivery: "unknown" } : message),
          }));
          void history.refresh(chatId);
          return false;
        }
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
