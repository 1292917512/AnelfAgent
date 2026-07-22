import { create } from "zustand";
import { useApprovalPopupStore } from "./approval-popup-store";
import { chatApi, workspaceApi } from "@/lib/api";
import i18n from "@/i18n";
import { useWorkbenchStore } from "./workbench-store";

export interface ChatMessage {
  role: string;
  content: string;
  timestamp?: string;
  id?: number;
  /** 客户端生成的稳定 key（SSE 消息无服务端 id 时使用） */
  /** 忙时发送的排队消息（agent 消费后清除标记） */
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

// ── SSE 单例（页面切换不断开） ──────────────────────────────────
let _eventSource: EventSource | null = null;
let _cidSeq = 0;
const nextCid = () => `c-${++_cidSeq}`;

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

/** 将 ui_command 事件分发到工作台 store */
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
  messages: ChatMessage[];
  sending: boolean;
  /** 进入发送态的时间戳（加载行计时用） */
  sendingSince: number | null;
  /** 流式过程状态（尾随兄弟，不落消息历史；回复到达时清除） */
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
  /** 上下文用量快照（状态栏显示；null=未知） */
  contextUsage: { tokens: number; threshold: number; window: number; percent: number } | null;
  pendingFiles: PendingFile[];
  historyLoaded: boolean;

  loadHistory: () => Promise<void>;
  startSSE: () => void;
  stopSSE: () => void;
  clearMessages: () => void;
  addFiles: (files: FileList | null) => Promise<void>;
  /** 以已在工作区内的路径附加文件（如从文件树拖入） */
  attachWorkspaceFile: (path: string, name: string) => void;
  removeFile: (idx: number) => void;
  send: (text: string, userName: string) => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  sending: false,
  sendingSince: null,
  streaming: null,
  contextUsage: null,
  pendingFiles: [],
  historyLoaded: false,

  loadHistory: async () => {
    if (get().historyLoaded) return;
    try {
      const r = await chatApi.history("web_user", 100);
      if (r.data?.length) {
        set({
          messages: r.data.map((m: Record<string, unknown>) => ({
            role: m.role as string,
            content: m.content as string,
            timestamp: m.timestamp as string,
            id: m.id as number,
          })),
        });
      }
    } catch { /* 历史加载失败保持空列表 */ }
    set({ historyLoaded: true });
  },

  startSSE: () => {
    if (_eventSource) return;
    const es = new EventSource("/api/chat/stream");
    _eventSource = es;

    es.addEventListener("reply", (e) => {
      try {
        const data = JSON.parse(e.data) as ChatMessage;
        set((s) => ({
          messages: [
            ...s.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
            { role: "assistant", content: data.content, cid: nextCid() },
          ],
          sending: false,
          sendingSince: null,
          streaming: null,
        }));
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("media", (e) => {
      try {
        const data = JSON.parse(e.data) as ChatMessage;
        set((s) => ({
          messages: [
            ...s.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
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
        }));
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("ui_command", (e) => {
      try {
        dispatchUiCommand(JSON.parse(e.data) as UiCommandPayload);
      } catch { /* 忽略非 JSON 帧 */ }
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
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("delta", (e) => {
      try {
        const data = JSON.parse(e.data);
        set((s) => {
          const cur = s.streaming && s.streaming.turnId === data.turn_id
            ? s.streaming
            : { turnId: data.turn_id, text: "", reasoning: "", tools: s.streaming?.tools ?? [], diffs: s.streaming?.diffs ?? [] };
          return {
            streaming: {
              ...cur,
              text: data.reasoning ? cur.text : cur.text + data.delta,
              reasoning: data.reasoning ? cur.reasoning + data.delta : cur.reasoning,
            },
          };
        });
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("tool_call", (e) => {
      try {
        const data = JSON.parse(e.data);
        set((s) => {
          const cur = s.streaming ?? { turnId: "", text: "", reasoning: "", tools: [], diffs: [] };
          const idx = cur.tools.findIndex((t) => t.call_id === data.call_id);
          const tools = [...cur.tools];
          const frame = {
            call_id: data.call_id, name: data.name, status: data.status,
            arguments: data.arguments, result_preview: data.result_preview,
            duration_ms: data.duration_ms,
          };
          if (idx >= 0) tools[idx] = { ...tools[idx], ...frame };
          else tools.push(frame);
          return { streaming: { ...cur, tools } };
        });
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("file_diff", (e) => {
      try {
        const data = JSON.parse(e.data);
        set((s) => {
          const cur = s.streaming ?? { turnId: "", text: "", reasoning: "", tools: [], diffs: [] };
          return {
            streaming: {
              ...cur,
              diffs: [...cur.diffs, { path: data.path, diff: data.diff, additions: data.additions, removals: data.removals }].slice(-3),
            },
          };
        });
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("context_usage", (e) => {
      try {
        const data = JSON.parse(e.data);
        set({ contextUsage: { tokens: data.tokens, threshold: data.threshold, window: data.window, percent: data.percent } });
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("ping", () => {});
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) _eventSource = null;
    };
  },

  stopSSE: () => {
    _eventSource?.close();
    _eventSource = null;
  },

  clearMessages: () => set({ messages: [] }),

  addFiles: async (files) => {
    if (!files) return;
    const newFiles: PendingFile[] = [];
    for (const file of Array.from(files)) {
      const type = classifyFile(file.name);
      const pf: PendingFile = { file, type, uploading: true };
      if (type === "image") pf.preview = URL.createObjectURL(file);
      newFiles.push(pf);
    }
    set((s) => ({ pendingFiles: [...s.pendingFiles, ...newFiles] }));

    for (const pf of newFiles) {
      try {
        const resp = await chatApi.upload(pf.file);
        const data = resp.data as { path: string; url: string };
        set((s) => ({
          pendingFiles: s.pendingFiles.map((f) => f.file === pf.file ? { ...f, uploading: false, path: data.path } : f),
        }));
      } catch {
        set((s) => ({
          pendingFiles: s.pendingFiles.map((f) => f.file === pf.file ? { ...f, uploading: false } : f),
        }));
      }
    }
  },

  attachWorkspaceFile: (path, name) => {
    const type = classifyFile(name);
    const stub = new File([], name);
    set((s) => ({
      pendingFiles: [...s.pendingFiles, {
        file: stub,
        type,
        uploading: false,
        path,
        // 工作区图片直接经 raw 接口预览缩略图
        preview: type === "image" ? workspaceApi.rawUrl(path) : undefined,
      }],
    }));
  },

  removeFile: (idx) => {
    set((s) => {
      const f = s.pendingFiles[idx];
      if (f?.preview) URL.revokeObjectURL(f.preview);
      return { pendingFiles: s.pendingFiles.filter((_, i) => i !== idx) };
    });
  },

  send: async (text, userName) => {
    const { pendingFiles } = get();
    const uploadedPaths = pendingFiles.filter((f) => f.path).map((f) => f.path!);
    if (!text.trim() && !uploadedPaths.length) return;

    const displayParts: string[] = [];
    if (text.trim()) displayParts.push(text.trim());
    for (const pf of pendingFiles) {
      if (pf.type === "image" && pf.preview) {
        displayParts.push(`![image](${pf.preview})`);
      } else {
        displayParts.push(`[${pf.type}: ${pf.file.name}]`);
      }
    }
    set((s) => ({
      messages: [...s.messages, {
        role: "user",
        content: displayParts.join("\n"),
        cid: nextCid(),
        queued: s.sending || undefined,
      }],
      pendingFiles: [],
      sending: true,
      sendingSince: Date.now(),
    }));

    try {
      await chatApi.send(text.trim() || " ", "web_user", userName, uploadedPaths.length ? uploadedPaths : undefined);
    } catch {
      set((s) => ({
        sending: false,
        sendingSince: null,
        messages: [...s.messages, { role: "system", content: i18n.t("sendFailed", { ns: "chat" }), cid: nextCid() }],
      }));
    }
  },
}));
