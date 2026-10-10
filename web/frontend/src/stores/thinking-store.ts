import { create } from "zustand";
import type { SessionSummary, ThinkingSession, TraceNode } from "@/lib/types";
import { thinkingApi } from "@/lib/api";
import { ThinkingStream, type ThinkingEvents } from "./thinking-stream";

const MAX_SESSIONS = 100;
type SessionPatch = (session: ThinkingSession) => ThinkingSession;

function upsertNode(nodes: TraceNode[], node: TraceNode): TraceNode[] {
  return nodes.some((item) => item.id === node.id)
    ? nodes.map((item) => item.id === node.id ? node : item)
    : [...nodes, node];
}

function orderedSessions(sessions: SessionSummary[]): SessionSummary[] {
  return [...new Map(sessions.map((session) => [session.id, session])).values()]
    .sort((a, b) => b.start_time - a.start_time).slice(0, MAX_SESSIONS);
}

interface ThinkingState {
  enabled: boolean;
  connected: boolean;
  statusSynced: boolean;
  toggling: boolean;
  statusError: unknown;
  sessions: SessionSummary[];
  sessionsLoading: boolean;
  sessionsError: unknown;
  activeSessionId: string | null;
  activeSession: ThinkingSession | null;
  sessionLoading: boolean;
  sessionError: unknown;
  selectedNodeId: string | null;
  autoFollow: boolean;
  initialize: (force?: boolean) => Promise<void>;
  shutdown: () => void;
  setTracking: (enabled: boolean) => Promise<void>;
  refreshSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
  refreshSession: () => Promise<void>;
  setSelectedNodeId: (id: string | null) => void;
  setAutoFollow: (value: boolean) => void;
  startSSE: () => void;
  stopSSE: () => void;
  handleSessionStart: (data: ThinkingEvents["session_start"]) => void;
  handleSessionEnd: (data: ThinkingEvents["session_end"]) => void;
  handleNodeAdded: (data: ThinkingEvents["node_added"]) => void;
  handleNodeUpdated: (data: ThinkingEvents["node_updated"]) => void;
  handleToolsUpdated: (data: ThinkingEvents["tools_updated"]) => void;
}

export const useThinkingStore = create<ThinkingState>((set, get) => {
  let initializing: Promise<void> | null = null;
  let requestId = 0;
  let generation = 0;
  let patches: SessionPatch[] | null = null;
  let listUpdates: Map<string, SessionSummary> | null = null;
  const stream = new ThinkingStream({
    session_start: (event) => get().handleSessionStart(event),
    session_end: (event) => get().handleSessionEnd(event),
    node_added: (event) => get().handleNodeAdded(event),
    node_updated: (event) => get().handleNodeUpdated(event),
    tools_updated: (event) => get().handleToolsUpdated(event),
  }, (connected) => set({ connected }), () => {
    void get().initialize(true);
    void get().refreshSessions();
    void get().refreshSession();
  });

  function patchSession(id: string, patch: SessionPatch): void {
    if (get().activeSessionId !== id) return;
    patches?.push(patch);
    const active = get().activeSession;
    if (active?.id === id) set({ activeSession: patch(active) });
  }

  function updateSummary(summary: SessionSummary): void {
    listUpdates?.set(summary.id, summary);
    set({ sessions: orderedSessions([...get().sessions.filter((item) => item.id !== summary.id), summary]) });
  }

  async function loadSession(id: string): Promise<void> {
    const ticket = ++requestId;
    patches = [];
    set({ sessionLoading: true, sessionError: null });
    try {
      const { data } = await thinkingApi.session(id);
      if (ticket !== requestId || get().activeSessionId !== id) return;
      const session = (patches ?? []).reduce((current, patch) => patch(current), data);
      set({ activeSession: session });
    } catch (error) {
      if (ticket === requestId) set({ sessionError: error });
    } finally {
      if (ticket === requestId) {
        patches = null;
        set({ sessionLoading: false });
      }
    }
  }

  return {
    enabled: false, connected: false, statusSynced: false, toggling: false, statusError: null,
    sessions: [], sessionsLoading: false, sessionsError: null,
    activeSessionId: null, activeSession: null, sessionLoading: false, sessionError: null,
    selectedNodeId: null, autoFollow: true,

    initialize: async (force = false) => {
      if (get().toggling || (get().statusSynced && !force)) return;
      if (initializing) return initializing;
      const ticket = generation;
      initializing = (async () => {
        set({ statusError: null });
        try {
          const { data } = await thinkingApi.status();
          if (ticket !== generation) return;
          const changed = get().statusSynced && get().enabled !== data.enabled;
          set({ enabled: data.enabled, statusSynced: true });
          if (data.enabled) stream.start(); else stream.stop();
          if (changed) {
            await get().refreshSessions();
            if (ticket === generation) await get().refreshSession();
          }
        } catch (error) { if (ticket === generation) set({ statusError: error }); }
        finally { if (ticket === generation) initializing = null; }
      })();
      return initializing;
    },
    shutdown: () => {
      generation += 1;
      requestId += 1;
      initializing = null;
      patches = null;
      listUpdates = null;
      stream.stop();
      set({ enabled: false, statusSynced: false, toggling: false, statusError: null,
        sessions: [], sessionsLoading: false, sessionsError: null, activeSessionId: null,
        activeSession: null, sessionLoading: false, sessionError: null, selectedNodeId: null,
      });
    },
    setTracking: async (enabled) => {
      if (get().toggling) return;
      const ticket = generation;
      set({ toggling: true, statusError: null });
      await initializing;
      if (ticket !== generation) return;
      try {
        const { data } = await thinkingApi.toggle(enabled);
        if (ticket !== generation) return;
        set({ enabled: data.enabled, statusSynced: true });
        if (data.enabled) stream.start();
        else stream.stop();
        await get().refreshSessions();
        await get().refreshSession();
      } catch (error) { if (ticket === generation) set({ statusError: error }); }
      finally { if (ticket === generation) set({ toggling: false }); }
    },
    refreshSessions: async () => {
      if (get().sessionsLoading) return;
      const ticket = generation;
      listUpdates = new Map();
      set({ sessionsLoading: true, sessionsError: null });
      try {
        const { data } = await thinkingApi.sessions(MAX_SESSIONS);
        if (ticket !== generation) return;
        const sessions = orderedSessions([...data.sessions, ...(listUpdates?.values() ?? [])]);
        set({ sessions });
        if (!get().activeSessionId && sessions[0]) {
          set({ activeSessionId: sessions[0].id });
          await loadSession(sessions[0].id);
        }
      } catch (error) { if (ticket === generation) set({ sessionsError: error }); }
      finally { if (ticket === generation) { listUpdates = null; set({ sessionsLoading: false }); } }
    },
    selectSession: async (id) => {
      set({ activeSessionId: id, activeSession: null, selectedNodeId: null, autoFollow: false });
      await loadSession(id);
    },
    refreshSession: async () => {
      const id = get().activeSessionId;
      if (id && !get().sessionLoading) await loadSession(id);
    },
    setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId, ...(selectedNodeId ? { autoFollow: false } : {}) }),
    setAutoFollow: (autoFollow) => set({ autoFollow }),
    startSSE: () => stream.start(),
    stopSSE: () => stream.stop(),

    handleSessionStart: ({ session, node }) => {
      updateSummary(session);
      const state = get();
      if ((!state.activeSessionId || state.autoFollow) && !session.is_heartbeat && !session.is_introspection && !session.is_delegation) {
        ++requestId;
        patches = null;
        set({ activeSessionId: session.id, activeSession: { ...session, nodes: [node], available_tools: [] },
          selectedNodeId: null, sessionLoading: false, sessionError: null });
      }
    },
    handleSessionEnd: ({ session_id, node, summary }) => {
      updateSummary(summary);
      patchSession(session_id, (session) => ({ ...session, ...summary, nodes: upsertNode(session.nodes, node) }));
    },
    handleNodeAdded: ({ session_id, node }) => {
      patchSession(session_id, (session) => {
        const nodes = upsertNode(session.nodes, node);
        return { ...session, nodes, node_count: nodes.length };
      });
    },
    handleNodeUpdated: ({ session_id, node_id, updates }) => {
      patchSession(session_id, (session) => ({ ...session, nodes: session.nodes.map((node) =>
        node.id === node_id ? { ...node, ...updates, data: { ...node.data, ...updates.data } } : node) }));
    },
    handleToolsUpdated: ({ session_id, tools }) => {
      patchSession(session_id, (session) => ({ ...session, available_tools: tools }));
    },
  };
});
