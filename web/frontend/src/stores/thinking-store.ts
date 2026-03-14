import { create } from "zustand";

const MAX_SESSIONS = 100;

// ── 类型定义 ────────────────────────────────────────────────────

export interface TraceNode {
  id: string;
  type: string;
  label: string;
  status: "pending" | "running" | "completed" | "error";
  timestamp: number;
  duration_ms: number | null;
  data: Record<string, unknown>;
  parent_id: string | null;
}

export interface SessionSummary {
  id: string;
  start_time: number;
  end_time: number | null;
  is_heartbeat: boolean;
  is_introspection?: boolean;
  node_count: number;
  ended: boolean;
  duration_ms: number | null;
}

export interface ThinkingSession extends SessionSummary {
  nodes: TraceNode[];
  available_tools: string[];
}

// SSE 连接管理（全局单例，不随页面切换断开）
let _eventSource: EventSource | null = null;
let _storeSetters: {
  setConnected: (v: boolean) => void;
  handleSessionStart: (data: { session: SessionSummary; node: TraceNode }) => void;
  handleSessionEnd: (data: { session_id: string; node: TraceNode; summary: SessionSummary }) => void;
  handleNodeAdded: (data: { session_id: string; node: TraceNode }) => void;
  handleNodeUpdated: (data: { session_id: string; node_id: string; updates: Record<string, unknown> }) => void;
  handleToolsUpdated: (data: { session_id: string; tools: string[] }) => void;
} | null = null;

function connectSSE(setters: typeof _storeSetters) {
  if (_eventSource) return;
  _storeSetters = setters;

  const es = new EventSource("/api/thinking/stream");
  _eventSource = es;

  es.onopen = () => setters?.setConnected(true);
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      setters?.setConnected(false);
      _eventSource = null;
    }
  };

  es.addEventListener("session_start", (e) => {
    try { setters?.handleSessionStart(JSON.parse(e.data)); } catch {}
  });
  es.addEventListener("session_end", (e) => {
    try { setters?.handleSessionEnd(JSON.parse(e.data)); } catch {}
  });
  es.addEventListener("node_added", (e) => {
    try { setters?.handleNodeAdded(JSON.parse(e.data)); } catch {}
  });
  es.addEventListener("node_updated", (e) => {
    try { setters?.handleNodeUpdated(JSON.parse(e.data)); } catch {}
  });
  es.addEventListener("tools_updated", (e) => {
    try { setters?.handleToolsUpdated(JSON.parse(e.data)); } catch {}
  });
  es.addEventListener("ping", () => {});
}

function disconnectSSE() {
  if (_eventSource) {
    _eventSource.close();
    _eventSource = null;
  }
  _storeSetters?.setConnected(false);
}

interface ThinkingState {
  enabled: boolean;
  connected: boolean;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  activeSession: ThinkingSession | null;
  selectedNodeId: string | null;
  autoFollow: boolean;
  _statusSynced: boolean;

  setEnabled: (v: boolean) => void;
  setConnected: (v: boolean) => void;
  setSessions: (s: SessionSummary[]) => void;
  setActiveSessionId: (id: string | null) => void;
  setActiveSession: (s: ThinkingSession | null) => void;
  setSelectedNodeId: (id: string | null) => void;
  setAutoFollow: (v: boolean) => void;
  setStatusSynced: (v: boolean) => void;
  startSSE: () => void;
  stopSSE: () => void;

  handleSessionStart: (data: { session: SessionSummary; node: TraceNode }) => void;
  handleSessionEnd: (data: { session_id: string; node: TraceNode; summary: SessionSummary }) => void;
  handleNodeAdded: (data: { session_id: string; node: TraceNode }) => void;
  handleNodeUpdated: (data: { session_id: string; node_id: string; updates: Record<string, unknown> }) => void;
  handleToolsUpdated: (data: { session_id: string; tools: string[] }) => void;
}

export const useThinkingStore = create<ThinkingState>((set, get) => ({
  enabled: false,
  connected: false,
  sessions: [],
  activeSessionId: null,
  activeSession: null,
  selectedNodeId: null,
  autoFollow: true,
  _statusSynced: false,

  setEnabled: (v) => set({ enabled: v }),
  setConnected: (v) => set({ connected: v }),
  setSessions: (s) => set({ sessions: s }),
  setActiveSessionId: (id) => set({ activeSessionId: id }),
  setActiveSession: (s) => set({ activeSession: s }),
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setAutoFollow: (v) => set({ autoFollow: v }),
  setStatusSynced: (v) => set({ _statusSynced: v }),

  startSSE: () => {
    const state = get();
    connectSSE({
      setConnected: state.setConnected,
      handleSessionStart: state.handleSessionStart,
      handleSessionEnd: state.handleSessionEnd,
      handleNodeAdded: state.handleNodeAdded,
      handleNodeUpdated: state.handleNodeUpdated,
      handleToolsUpdated: state.handleToolsUpdated,
    });
  },
  stopSSE: () => {
    disconnectSSE();
  },

  handleSessionStart: ({ session, node }) => {
    set((state) => {
      const sessions = [session, ...state.sessions].slice(0, MAX_SESSIONS);
      const newSession: ThinkingSession = { ...session, nodes: [node], available_tools: [] };
      if (state.autoFollow) {
        return { sessions, activeSessionId: session.id, activeSession: newSession };
      }
      return { sessions };
    });
  },

  handleSessionEnd: ({ session_id, node, summary }) => {
    set((state) => {
      const sessions = state.sessions.map((s) =>
        s.id === session_id ? { ...s, ...summary } : s,
      );
      if (state.activeSessionId === session_id && state.activeSession) {
        return {
          sessions,
          activeSession: {
            ...state.activeSession,
            ...summary,
            nodes: [...state.activeSession.nodes, node],
          },
        };
      }
      return { sessions };
    });
  },

  handleNodeAdded: ({ session_id, node }) => {
    set((state) => {
      if (state.activeSessionId !== session_id || !state.activeSession) return {};
      return {
        activeSession: {
          ...state.activeSession,
          node_count: state.activeSession.node_count + 1,
          nodes: [...state.activeSession.nodes, node],
        },
      };
    });
  },

  handleNodeUpdated: ({ session_id, node_id, updates }) => {
    set((state) => {
      if (state.activeSessionId !== session_id || !state.activeSession) return {};
      const nodes = state.activeSession.nodes.map((n) =>
        n.id === node_id ? { ...n, ...updates } : n,
      );
      return { activeSession: { ...state.activeSession, nodes } };
    });
  },

  handleToolsUpdated: ({ session_id, tools }) => {
    set((state) => {
      if (state.activeSessionId !== session_id || !state.activeSession) return {};
      return { activeSession: { ...state.activeSession, available_tools: tools } };
    });
  },
}));
