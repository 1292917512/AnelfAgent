import { create } from "zustand";
import type { ContextSnapshotData, PlanRecord, SessionSummary, ThinkingSession, TraceNode } from "@/lib/types";
import { usePlanStore } from "./plan-store";
import { useChatStore } from "./chat-store";

// 类型定义集中在 lib/types/thinking.ts，此处 re-export 兼容既有 import 路径
export type { SessionSummary, ThinkingSession, TraceNode } from "@/lib/types";

const MAX_SESSIONS = 100;

// SSE 连接管理（全局单例，不随页面切换断开）
let _eventSource: EventSource | null = null;
let _storeSetters: {
  setConnected: (v: boolean) => void;
  handleSessionStart: (data: { session: SessionSummary; node: TraceNode }) => void;
  handleSessionEnd: (data: { session_id: string; node: TraceNode; summary: SessionSummary }) => void;
  handleNodeAdded: (data: { session_id: string; node: TraceNode }) => void;
  handleNodeUpdated: (data: { session_id: string; node_id: string; updates: Partial<TraceNode> }) => void;
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

  // 上下文快照（跨页面持久）
  snapshotArmed: boolean;
  snapshotData: ContextSnapshotData | null;
  showSnapshot: boolean;

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

  setSnapshotArmed: (v: boolean) => void;
  setSnapshotData: (d: ContextSnapshotData | null) => void;
  setShowSnapshot: (v: boolean) => void;
  clearSnapshot: () => void;

  handleSessionStart: (data: { session: SessionSummary; node: TraceNode }) => void;
  handleSessionEnd: (data: { session_id: string; node: TraceNode; summary: SessionSummary }) => void;
  handleNodeAdded: (data: { session_id: string; node: TraceNode }) => void;
  handleNodeUpdated: (data: { session_id: string; node_id: string; updates: Partial<TraceNode> }) => void;
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

  snapshotArmed: false,
  snapshotData: null,
  showSnapshot: false,

  setEnabled: (v) => set({ enabled: v }),
  setConnected: (v) => set({ connected: v }),
  setSessions: (s) => set({ sessions: s }),
  setActiveSessionId: (id) => set({ activeSessionId: id }),
  setActiveSession: (s) => set({ activeSession: s }),
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setAutoFollow: (v) => set({ autoFollow: v }),
  setStatusSynced: (v) => set({ _statusSynced: v }),

  setSnapshotArmed: (v) => set({ snapshotArmed: v }),
  setSnapshotData: (d) => set({ snapshotData: d }),
  setShowSnapshot: (v) => set({ showSnapshot: v }),
  clearSnapshot: () => set({ snapshotArmed: false, snapshotData: null, showSnapshot: false }),

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

// ------------------------------------------------------------------
// Plan 虚拟节点注入：把 plan-store 中的 plan 转为 TraceNode 拼到 activeSession.nodes
// 使 FlowView 能看到"plan_root → plan_step × N"的子树
// ------------------------------------------------------------------

const PLAN_NODE_ID_PREFIX = "__plan__";

function planRootNodeId(planId: string): string {
  return `${PLAN_NODE_ID_PREFIX}root:${planId}`;
}

function planStepNodeId(planId: string, stepIndex: number): string {
  return `${PLAN_NODE_ID_PREFIX}step:${planId}:${stepIndex}`;
}

function planStepStatusToNodeStatus(status: string): TraceNode["status"] {
  if (status === "completed") return "completed";
  if (status === "in_progress") return "running";
  if (status === "skipped") return "error";
  return "pending";
}

/**
 * 把指定 chat 的 plan 转成 TraceNode[]，挂在 activeSession 末端。
 * 保持幂等：以 plan_id 为 key，每次 plan-store 变化时全量重建。
 */
export function buildPlanVirtualNodes(chatPlans: Record<string, PlanRecord> | undefined): TraceNode[] {
  if (!chatPlans) return [];
  const plans = Object.values(chatPlans).sort((a, b) => a.created_at - b.created_at);
  const nodes: TraceNode[] = [];
  for (const plan of plans) {
    const rootId = planRootNodeId(plan.plan_id);
    nodes.push({
      id: rootId,
      type: "plan_root",
      label: plan.goal || `Plan ${plan.plan_id}`,
      status: plan.status === "completed" ? "completed"
        : plan.status === "cancelled" ? "error"
          : "running",
      timestamp: plan.created_at,
      duration_ms: plan.completed_at
        ? Math.round((plan.completed_at - plan.created_at) * 1000)
        : null,
      data: {
        plan_id: plan.plan_id,
        goal: plan.goal,
        step_count: plan.steps.length,
        files: plan.files,
        risks: plan.risks,
      },
      parent_id: null,
    });
    for (const step of plan.steps) {
      nodes.push({
        id: planStepNodeId(plan.plan_id, step.index),
        type: "plan_step",
        label: step.content,
        status: planStepStatusToNodeStatus(step.status),
        timestamp: plan.created_at + step.index * 0.001,
        duration_ms: null,
        data: {
          plan_id: plan.plan_id,
          step_index: step.index,
          note: step.note,
          step_status: step.status,
        },
        parent_id: rootId,
      });
    }
  }
  return nodes;
}

// useMergedActiveSessionNodes 的结果缓存：输入引用未变时返回同一数组引用，
// 保证 FlowView 的 useMemo([session, mergedNodes]) 链不被无意义的新数组击穿。
let _mergedCache: {
  session: ThinkingSession | null;
  chatPlans: Record<string, PlanRecord> | undefined;
  result: TraceNode[];
} | null = null;

/**
 * 订阅 plan-store 变化，把 plan 虚拟节点合并进 activeSession.nodes。
 * 在 FlowView / TimelineView 渲染前调用，保证 plan 节点实时同步。
 */
export function useMergedActiveSessionNodes(): TraceNode[] {
  const activeSession = useThinkingStore((s) => s.activeSession);
  const activeChatId = useChatStore((s) => s.activeChatId);
  const chatPlans = usePlanStore((s) => s.plans[activeChatId]);

  if (_mergedCache && _mergedCache.session === activeSession && _mergedCache.chatPlans === chatPlans) {
    return _mergedCache.result;
  }

  let result: TraceNode[];
  if (!activeSession) {
    // 即使没有 thinking session，也展示 plan 节点（让 PlanCard 也能在导图里看到）
    result = buildPlanVirtualNodes(chatPlans);
  } else {
    // 真实 trace 节点（剔除之前的 plan 虚拟节点，避免重复）
    const realNodes = activeSession.nodes.filter((n) => !n.id.startsWith(PLAN_NODE_ID_PREFIX));
    const planNodes = buildPlanVirtualNodes(chatPlans);
    // 简单合并：plan 节点附加在末尾（FlowView 的递归布局会按 parent_id 自动放置）
    result = [...realNodes, ...planNodes];
  }
  // eslint-disable-next-line react-hooks/globals -- 有意的模块级 memo：保证输入不变时返回同一引用，供 FlowView 的 useMemo 链复用
  _mergedCache = { session: activeSession, chatPlans, result };
  return result;
}

