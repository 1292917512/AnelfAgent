/**
 * Chat store — 按 chat_id 分桶的对话状态。
 *
 * 关键设计：
 * - buckets[chat_id] 持有该会话的 messages / streaming / pendingFiles 等全部状态
 * - activeChatId 控制当前激活的会话（Header tab / 新建 / 切换）
 * - SSE 单连接：所有事件带 chat_id，路由到对应 bucket
 * - plan / delegation 事件分流到 plan-store / delegation-store（按 chat_id 维度）
 *
 * 兼容：默认 chat_id = "default"（即旧 scope=user_web_user），历史数据无缝衔接。
 */
import { create } from "zustand";
import { useApprovalPopupStore } from "./approval-popup-store";
import { usePlanStore } from "./plan-store";
import { useDelegationStore } from "./delegation-store";
import { chatApi, workspaceApi } from "@/lib/api";
import i18n from "@/i18n";
import { useWorkbenchStore } from "./workbench-store";
import type { ChatMeta, PlanRecord, DelegationNode } from "@/lib/types";

export interface ChatMessage {
  role: string;
  content: string;
  timestamp?: string;
  id?: number;
  queued?: boolean;
  cid?: string;
  media_type?: string;
  url?: string;
  caption?: string;
}

export interface PendingFile {
  file: File;
  preview?: string;
  type: string;
  uploading: boolean;
  path?: string;
}

export function classifyFile(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"].includes(ext)) return "image";
  if (["mp3", "wav", "ogg", "flac", "m4a", "amr", "opus"].includes(ext)) return "audio";
  if (["mp4", "avi", "mkv", "mov", "webm", "flv"].includes(ext)) return "video";
  return "file";
}

export interface ChatBucket {
  messages: ChatMessage[];
  sending: boolean;
  sendingSince: number | null;
  streaming: {
    turnId: string;
    text: string;
    reasoning: string;
    tools: {
      call_id: string;
      name: string;
      status: "running" | "done" | "error";
      arguments?: string;
      result_preview?: string;
      duration_ms?: number;
    }[];
    diffs: { path: string; diff: string; additions: number; removals: number }[];
  } | null;
  pendingFiles: PendingFile[];
  historyLoaded: boolean;
  /** 非激活会话收到新消息时的未读计数（切换会话时清零） */
  unread: number;
}

function emptyBucket(): ChatBucket {
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

// ── SSE 单例 ──────────────────────────────────────────────────
let _eventSource: EventSource | null = null;
let _cidSeq = 0;
const nextCid = () => `c-${++_cidSeq}`;

const DEFAULT_CHAT_ID = "default";
const LOCAL_STORAGE_ACTIVE_KEY = "anelf:activeChatId";
const LOCAL_STORAGE_CHATS_KEY = "anelf:chats";

function loadActiveChatId(): string {
  try {
    return localStorage.getItem(LOCAL_STORAGE_ACTIVE_KEY) || DEFAULT_CHAT_ID;
  } catch {
    return DEFAULT_CHAT_ID;
  }
}

function persistActiveChatId(chatId: string) {
  try {
    localStorage.setItem(LOCAL_STORAGE_ACTIVE_KEY, chatId);
  } catch { /* ignore */ }
}

function loadChatsFromStorage(): ChatMeta[] {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_CHATS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as ChatMeta[];
  } catch {
    return [];
  }
}

function persistChats(chats: ChatMeta[]) {
  try {
    localStorage.setItem(LOCAL_STORAGE_CHATS_KEY, JSON.stringify(chats));
  } catch { /* ignore */ }
}

function genChatId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** 发送看门狗：120s 无 reply/delta 则复位发送态 */
let _sendWatchdog: ReturnType<typeof setTimeout> | null = null;
const SEND_TIMEOUT_MS = 120_000;

function clearSendWatchdog() {
  if (_sendWatchdog) {
    clearTimeout(_sendWatchdog);
    _sendWatchdog = null;
  }
}

function armSendWatchdog(chatId: string) {
  clearSendWatchdog();
  _sendWatchdog = setTimeout(() => {
    _sendWatchdog = null;
    const s = useChatStore.getState();
    const bucket = s.buckets[chatId];
    if (!bucket?.sending) return;
    useChatStore.setState((prev) => ({
      buckets: {
        ...prev.buckets,
        [chatId]: {
          ...bucket,
          sending: false,
          sendingSince: null,
          streaming: null,
          messages: [
            ...bucket.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
            { role: "system", content: i18n.t("sendTimeout", { ns: "chat" }), cid: nextCid() },
          ],
        },
      },
    }));
  }, SEND_TIMEOUT_MS);
}

const MAX_FILES = 9;
const MAX_FILE_SIZE = 50 * 1024 * 1024;

interface UiCommandPayload {
  command: string;
  id?: string;
  title?: string;
  content?: string;
  level?: string;
  ts?: number;
  ask_id?: string;
  question?: string;
  options?: string[];
  panel?: string;
  payload?: string;
  text?: string;
}

function dispatchUiCommand(data: UiCommandPayload) {
  const wb = useWorkbenchStore.getState();
  switch (data.command) {
    case "notify":
      wb.pushNotification({
        id: data.id || nextCid(),
        title: data.title || "",
        content: data.content || "",
        level: (data.level as "info" | "success" | "warning" | "error") || "info",
        ts: data.ts || Date.now() / 1000,
      });
      break;
    case "ask":
      if (data.ask_id && data.question) {
        wb.pushAsk({
          ask_id: data.ask_id,
          question: data.question,
          options: data.options || [],
          ts: data.ts || Date.now() / 1000,
        });
      }
      break;
    case "open_panel":
      if (data.panel) wb.openPanel(data.panel, data.payload || "");
      break;
    case "compose":
      if (data.text) wb.setDraft(data.text);
      break;
  }
}

interface ChatState {
  buckets: Record<string, ChatBucket>;
  activeChatId: string;
  chats: ChatMeta[];
  contextUsage: { tokens: number; threshold: number; window: number; percent: number } | null;

  /** 派生 helper：当前激活 bucket（渲染时直接用） */
  active: () => ChatBucket;

  setActiveChat: (chatId: string) => void;
  newChat: (title?: string) => string;
  removeChat: (chatId: string) => void;
  renameChat: (chatId: string, title: string) => void;

  loadChats: () => Promise<void>;
  loadHistory: (chatId?: string) => Promise<void>;
  startSSE: () => void;
  stopSSE: () => void;
  clearMessages: () => void;
  addFiles: (files: FileList | null) => Promise<void>;
  attachWorkspaceFile: (path: string, name: string) => void;
  removeFile: (idx: number) => void;
  send: (text: string, userName: string) => Promise<boolean>;
}

export const useChatStore = create<ChatState>((set, get) => {
  const initialChatId = loadActiveChatId();
  const initialChats = loadChatsFromStorage();
  const initialBuckets: Record<string, ChatBucket> = {};
  if (initialChats.length === 0) {
    initialBuckets[DEFAULT_CHAT_ID] = emptyBucket();
    initialChats.push({
      chat_id: DEFAULT_CHAT_ID,
      title: i18n.t("defaultChat", { ns: "chat", defaultValue: "默认会话" }),
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
      title: i18n.t("defaultChat", { ns: "chat", defaultValue: "默认会话" }),
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
        title: title || i18n.t("newChat", { ns: "chat", defaultValue: "新会话" }),
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
              title: c.title || "新会话",
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
        const r = await chatApi.history(scopeId, 100, targetChatId === DEFAULT_CHAT_ID ? undefined : targetChatId);
        if (r.data?.length) {
          updateBucket(targetChatId, () => ({
            messages: r.data.map((m: Record<string, unknown>) => ({
              role: m.role as string,
              content: m.content as string,
              timestamp: m.timestamp as string,
              id: m.id as number,
            })),
          }));
        }
      } catch { /* ignore */ }
      updateBucket(targetChatId, () => ({ historyLoaded: true }));
    },

    startSSE: () => {
      if (_eventSource) return;
      const es = new EventSource("/api/chat/stream");
      _eventSource = es;

      const routeChatId = (data: Record<string, unknown>): string => {
        const cid = typeof data.chat_id === "string" && data.chat_id ? data.chat_id : DEFAULT_CHAT_ID;
        return cid;
      };

      es.addEventListener("reply", (e) => {
        try {
          const data = JSON.parse(e.data) as ChatMessage & { chat_id?: string };
          clearSendWatchdog();
          const chatId = routeChatId(data as unknown as Record<string, unknown>);
          const isBackground = chatId !== get().activeChatId;
          updateBucket(chatId, (b) => ({
            messages: [
              ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
              { role: "assistant", content: data.content, cid: nextCid() },
            ],
            sending: false,
            sendingSince: null,
            streaming: null,
            unread: isBackground ? b.unread + 1 : b.unread,
          }));
        } catch { /* ignore */ }
      });

      es.addEventListener("turn_end", (e) => {
        clearSendWatchdog();
        try {
          const data = e.data ? JSON.parse(e.data) : {};
          const chatId = routeChatId(data as Record<string, unknown>);
          updateBucket(chatId, () => ({
            sending: false,
            sendingSince: null,
            streaming: null,
          }));
        } catch {
          // 无 chat_id：对所有 sending bucket 复位（兜底）
          const s = get();
          for (const cid of Object.keys(s.buckets)) {
            updateBucket(cid, () => ({ sending: false, sendingSince: null, streaming: null }));
          }
        }
      });

      es.addEventListener("media", (e) => {
        try {
          const data = JSON.parse(e.data) as ChatMessage & { chat_id?: string };
          clearSendWatchdog();
          const chatId = routeChatId(data as unknown as Record<string, unknown>);
          const isBackground = chatId !== get().activeChatId;
          updateBucket(chatId, (b) => ({
            messages: [
              ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
              {
                role: "assistant",
                content: data.caption || "",
                cid: nextCid(),
                media_type: data.media_type,
                url: data.url,
                caption: data.caption,
              },
            ],
            sending: false,
            sendingSince: null,
            streaming: null,
            unread: isBackground ? b.unread + 1 : b.unread,
          }));
        } catch { /* ignore */ }
      });

      es.addEventListener("ui_command", (e) => {
        try {
          dispatchUiCommand(JSON.parse(e.data) as UiCommandPayload);
        } catch { /* ignore */ }
      });

      es.addEventListener("approval_request", (e) => {
        try {
          const data = JSON.parse(e.data);
          useApprovalPopupStore.getState().push({
            request_id: data.request_id,
            tool_name: data.tool_name,
            tool_args: data.tool_args ?? "",
            risk_level: data.risk_level ?? "medium",
            reason: data.reason ?? "",
            timeout_seconds: data.timeout_seconds ?? 60,
            received_at: Date.now(),
          });
        } catch { /* ignore */ }
      });

      es.addEventListener("delta", (e) => {
        try {
          const data = JSON.parse(e.data);
          clearSendWatchdog();
          const chatId = routeChatId(data as Record<string, unknown>);
          updateBucket(chatId, (b) => {
            const cur = b.streaming && b.streaming.turnId === data.turn_id
              ? b.streaming
              : { turnId: data.turn_id, text: "", reasoning: "", tools: [], diffs: [] };
            return {
              streaming: {
                ...cur,
                text: data.reasoning ? cur.text : cur.text + data.delta,
                reasoning: data.reasoning ? cur.reasoning + data.delta : cur.reasoning,
              },
            };
          });
        } catch { /* ignore */ }
      });

      es.addEventListener("tool_call", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          updateBucket(chatId, (b) => {
            const turnId = data.turn_id ?? "";
            const cur = b.streaming && b.streaming.turnId === turnId
              ? b.streaming
              : { turnId, text: "", reasoning: "", tools: [], diffs: [] };
            const idx = cur.tools.findIndex((t) => t.call_id === data.call_id);
            const tools = [...cur.tools];
            const frame = {
              call_id: data.call_id,
              name: data.name,
              status: data.status,
              arguments: data.arguments,
              result_preview: data.result_preview,
              duration_ms: data.duration_ms,
            };
            if (idx >= 0) tools[idx] = { ...tools[idx], ...frame };
            else tools.push(frame);
            return { streaming: { ...cur, tools } };
          });
        } catch { /* ignore */ }
      });

      es.addEventListener("file_diff", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          updateBucket(chatId, (b) => {
            const turnId = data.turn_id ?? "";
            const cur = b.streaming && b.streaming.turnId === turnId
              ? b.streaming
              : { turnId, text: "", reasoning: "", tools: [], diffs: [] };
            return {
              streaming: {
                ...cur,
                diffs: [...cur.diffs, {
                  path: data.path,
                  diff: data.diff,
                  additions: data.additions,
                  removals: data.removals,
                }].slice(-3),
              },
            };
          });
        } catch { /* ignore */ }
      });

      es.addEventListener("context_usage", (e) => {
        try {
          const data = JSON.parse(e.data);
          set({
            contextUsage: {
              tokens: data.tokens,
              threshold: data.threshold,
              window: data.window,
              percent: data.percent,
            },
          });
        } catch { /* ignore */ }
      });

      // ── Plan 模式事件 ────────────────────────────────────────────
      es.addEventListener("plan_submitted", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          const plan: PlanRecord = {
            plan_id: data.plan_id,
            chat_id: chatId,
            goal: data.goal ?? "",
            steps: (data.steps ?? []).map((s: Record<string, unknown>) => ({
              index: s.index as number,
              content: s.content as string,
              status: (s.status as PlanRecord["steps"][number]["status"]) ?? "pending",
              note: (s.note as string) ?? "",
            })),
            files: data.files ?? "",
            risks: data.risks ?? "",
            status: "executing",
            created_at: data.ts ?? Date.now() / 1000,
            updated_at: Date.now() / 1000,
          };
          usePlanStore.getState().upsertPlan(plan);
          // 新 plan 出现时自动展开浮窗
          usePlanStore.getState().setPanelHidden(false);
          usePlanStore.getState().setPanelCollapsed(false);
        } catch { /* ignore */ }
      });

      es.addEventListener("plan_step_updated", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          usePlanStore.getState().updatePlanStep(
            chatId,
            data.plan_id,
            data.step_index,
            data.step_status,
            data.note,
          );
          // 阶段反馈：**只在 AI 精确标记时**插入系统消息（"自动推进" / "会话结束"
          // 事件由程序兜底触发，每轮一次，全部插入会刷屏；PlanCard/PlanPanel
          // 已实时反映状态，无需重复播报）
          if (data.note === "自动推进" || data.note === "会话结束自动收束" || data.note === "会话结束未执行") {
            return;
          }
          const plan = usePlanStore.getState().plans[chatId]?.[data.plan_id];
          if (plan && data.step_status !== "pending") {
            const step = plan.steps.find((s) => s.index === data.step_index);
            const stepText = step?.content ?? `步骤 ${(data.step_index ?? 0) + 1}`;
            const icon = data.step_status === "completed" ? "✓"
              : data.step_status === "skipped" ? "⊘"
                : "→";
            updateBucket(chatId, (b) => ({
              messages: [...b.messages, {
                role: "system",
                content: `${icon} 计划步骤 ${(data.step_index ?? 0) + 1}/${plan.steps.length}: ${stepText}${data.note ? ` — ${data.note}` : ""}`,
                cid: nextCid(),
              }],
            }));
          }
        } catch { /* ignore */ }
      });

      es.addEventListener("plan_status_changed", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          const status = data.goal_status === "completed"
            ? "completed"
            : data.goal_status === "cancelled"
              ? "cancelled"
              : "executing";
          usePlanStore.getState().updatePlanStatus(chatId, data.plan_id, status);
          // 整体完成/取消时插入消息
          if (status === "completed" || status === "cancelled") {
            const plan = usePlanStore.getState().plans[chatId]?.[data.plan_id];
            const goalText = plan?.goal ?? "计划";
            updateBucket(chatId, (b) => ({
              messages: [...b.messages, {
                role: "system",
                content: status === "completed"
                  ? `✓ 计划完成：${goalText}`
                  : `⊘ 计划已取消：${goalText}`,
                cid: nextCid(),
              }],
            }));
          }
        } catch { /* ignore */ }
      });

      es.addEventListener("plan_cancelled", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          usePlanStore.getState().updatePlanStatus(chatId, data.plan_id, "cancelled", data.reason);
        } catch { /* ignore */ }
      });

      // ── 子代理事件 ───────────────────────────────────────────────
      es.addEventListener("delegation_started", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          const node: DelegationNode = {
            delegation_id: data.delegation_id,
            chat_id: chatId,
            goal: data.goal ?? "",
            context_preview: data.context_preview ?? "",
            role: (data.role as DelegationNode["role"]) ?? "leaf",
            task_index: data.task_index ?? 0,
            background: !!data.background,
            depth: data.depth ?? 0,
            status: "running",
            started_at: data.ts ?? Date.now() / 1000,
          };
          useDelegationStore.getState().upsertDelegation(node);
        } catch { /* ignore */ }
      });

      es.addEventListener("delegation_resolved", (e) => {
        try {
          const data = JSON.parse(e.data);
          const chatId = routeChatId(data as Record<string, unknown>);
          useDelegationStore.getState().resolveDelegation(
            chatId,
            data.delegation_id,
            !!data.success,
            data.output,
            data.error,
          );
        } catch { /* ignore */ }
      });

      es.addEventListener("ping", () => {});
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
      updateBucket(chatId, () => ({ messages: [] }));
    },

    addFiles: async (files) => {
      if (!files) return;
      const chatId = get().activeChatId;
      const bucket = get().buckets[chatId] ?? emptyBucket();
      const current = bucket.pendingFiles;
      const incoming = Array.from(files);
      const accepted: File[] = [];
      for (const file of incoming) {
        if (current.length + accepted.length >= MAX_FILES) {
          useWorkbenchStore.getState().pushNotification({
            id: nextCid(),
            title: "",
            content: i18n.t("fileLimit", { ns: "chat", max: MAX_FILES }),
            level: "warning",
            ts: Date.now() / 1000,
          });
          break;
        }
        if (file.size > MAX_FILE_SIZE) {
          useWorkbenchStore.getState().pushNotification({
            id: nextCid(),
            title: "",
            content: i18n.t("fileTooLarge", { ns: "chat", name: file.name, max: 50 }),
            level: "warning",
            ts: Date.now() / 1000,
          });
          continue;
        }
        accepted.push(file);
      }
      if (!accepted.length) return;
      const newFiles: PendingFile[] = [];
      for (const file of accepted) {
        const type = classifyFile(file.name);
        const pf: PendingFile = { file, type, uploading: true };
        if (type === "image") pf.preview = URL.createObjectURL(file);
        newFiles.push(pf);
      }
      updateBucket(chatId, (b) => ({ pendingFiles: [...b.pendingFiles, ...newFiles] }));

      for (const pf of newFiles) {
        try {
          const resp = await chatApi.upload(pf.file);
          const data = resp.data as { path: string; url: string };
          updateBucket(chatId, (b) => ({
            pendingFiles: b.pendingFiles.map((f) =>
              f.file === pf.file ? { ...f, uploading: false, path: data.path } : f,
            ),
          }));
        } catch {
          updateBucket(chatId, (b) => ({
            pendingFiles: b.pendingFiles.map((f) =>
              f.file === pf.file ? { ...f, uploading: false } : f,
            ),
          }));
        }
      }
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
        if (f?.preview) URL.revokeObjectURL(f.preview);
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

      for (const pf of pendingFiles) {
        if (pf.preview?.startsWith("blob:")) URL.revokeObjectURL(pf.preview);
      }

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

      armSendWatchdog(chatId);

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
            { role: "system", content: i18n.t("sendFailed", { ns: "chat" }), cid: nextCid() },
          ],
        }));
        return false;
      }
    },
  };
});

// 兼容旧代码：默认 active bucket 的 messages / sending / streaming / pendingFiles / contextUsage
// 推荐新代码用 buckets[activeChatId] 直接访问，或 active() helper。
