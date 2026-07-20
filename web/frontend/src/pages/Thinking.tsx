import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeTypes,
  BackgroundVariant,
  useReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useThinkingStore, type ThinkingSession } from "@/stores/thinking-store";
import { thinkingApi } from "@/lib/api";
import { SessionList } from "@/components/thinking/SessionList";
import { NodeDetail } from "@/components/thinking/NodeDetail";
import { ToolsPanel } from "@/components/thinking/ToolsPanel";
import TraceNodeComponent from "@/components/thinking/TraceNode";
import { useIsMobile } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";
import {
  Power,
  PowerOff,
  Crosshair,
  RefreshCw,
  PanelLeftOpen,
  PanelLeftClose,
  List,
} from "lucide-react";

const NODE_TYPES: NodeTypes = {
  trace: TraceNodeComponent,
};

const NODE_HEIGHT = 60;
const V_GAP = 24;
const H_GAP = 240;

function edgeStroke(status: string): string {
  if (status === "error") return "var(--danger)";
  return "var(--border-strong)";
}

/**
 * 构建 DAG 布局：利用 parent_id 实现分支。
 * 同一 parent 的多个子节点横向排列（并行分支），其余纵向推进。
 */
function buildFlowElements(session: ThinkingSession | null) {
  if (!session) return { nodes: [] as Node[], edges: [] as Edge[] };

  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const nodeMap = new Map<string, { x: number; y: number }>();

  const childrenMap = new Map<string, typeof session.nodes>();
  const rootNodes: typeof session.nodes = [];

  for (const n of session.nodes) {
    if (n.parent_id && session.nodes.some((p) => p.id === n.parent_id)) {
      const list = childrenMap.get(n.parent_id) ?? [];
      list.push(n);
      childrenMap.set(n.parent_id, list);
    } else {
      rootNodes.push(n);
    }
  }

  let nextY = 0;

  for (const n of rootNodes) {
    const x = 0;
    const y = nextY;
    nodeMap.set(n.id, { x, y });
    nodes.push({
      id: n.id,
      type: "trace",
      position: { x, y },
      data: {
        label: n.label,
        nodeType: n.type,
        status: n.status,
        duration_ms: n.duration_ms,
        data: n.data,
      },
    });

    const children = childrenMap.get(n.id);
    if (children && children.length > 0) {
      const childY = y + NODE_HEIGHT + V_GAP;
      const totalWidth = (children.length - 1) * H_GAP;
      const startX = x - totalWidth / 2;

      let maxChildBottom = childY;

      for (let ci = 0; ci < children.length; ci++) {
        const child = children[ci]!;
        const cx = startX + ci * H_GAP;
        const cy = childY;
        nodeMap.set(child.id, { x: cx, y: cy });
        nodes.push({
          id: child.id,
          type: "trace",
          position: { x: cx, y: cy },
          data: {
            label: child.label,
            nodeType: child.type,
            status: child.status,
            duration_ms: child.duration_ms,
            data: child.data,
          },
        });
        edges.push({
          id: `e-${n.id}-${child.id}`,
          source: n.id,
          target: child.id,
          animated: child.status === "running",
          style: { stroke: edgeStroke(child.status), strokeWidth: 1.5 },
        });

        const grandChildren = childrenMap.get(child.id);
        if (grandChildren && grandChildren.length > 0) {
          let gcY = cy + NODE_HEIGHT + V_GAP;
          for (const gc of grandChildren) {
            nodeMap.set(gc.id, { x: cx, y: gcY });
            nodes.push({
              id: gc.id,
              type: "trace",
              position: { x: cx, y: gcY },
              data: {
                label: gc.label,
                nodeType: gc.type,
                status: gc.status,
                duration_ms: gc.duration_ms,
                data: gc.data,
              },
            });
            edges.push({
              id: `e-${child.id}-${gc.id}`,
              source: child.id,
              target: gc.id,
              animated: gc.status === "running",
              style: { stroke: edgeStroke(gc.status), strokeWidth: 1.5 },
            });
            gcY += NODE_HEIGHT + V_GAP;
          }
          maxChildBottom = Math.max(maxChildBottom, gcY - V_GAP);
        } else {
          maxChildBottom = Math.max(maxChildBottom, cy + NODE_HEIGHT);
        }
      }
      nextY = maxChildBottom + V_GAP;
    } else {
      nextY = y + NODE_HEIGHT + V_GAP;
    }
  }

  const placed = new Set(nodes.map((n) => n.id));
  for (const n of session.nodes) {
    if (!placed.has(n.id)) {
      nodeMap.set(n.id, { x: 0, y: nextY });
      nodes.push({
        id: n.id,
        type: "trace",
        position: { x: 0, y: nextY },
        data: {
          label: n.label,
          nodeType: n.type,
          status: n.status,
          duration_ms: n.duration_ms,
          data: n.data,
        },
      });
      nextY += NODE_HEIGHT + V_GAP;
    }
  }

  for (let i = 0; i < session.nodes.length; i++) {
    const n = session.nodes[i]!;
    if (n.parent_id && session.nodes.some((p) => p.id === n.parent_id)) continue;
    const rootIdx = rootNodes.indexOf(n);
    if (rootIdx > 0) {
      const prev = rootNodes[rootIdx - 1]!;
      const edgeId = `e-${prev.id}-${n.id}`;
      if (!edges.some((e) => e.id === edgeId)) {
        const lastDescendant = findLastDescendant(prev.id, childrenMap, session.nodes);
        edges.push({
          id: `e-${lastDescendant}-${n.id}`,
          source: lastDescendant,
          target: n.id,
          animated: n.status === "running",
          style: { stroke: edgeStroke(n.status), strokeWidth: 1.5, strokeDasharray: n.parent_id ? undefined : "4 2" },
        });
      }
    }
  }

  return { nodes, edges };
}

function findLastDescendant(
  nodeId: string,
  childrenMap: Map<string, { id: string }[]>,
  allNodes: { id: string }[],
): string {
  const children = childrenMap.get(nodeId);
  if (!children || children.length === 0) return nodeId;
  const lastChild = children[children.length - 1]!;
  const grandChildren = childrenMap.get(lastChild.id);
  if (grandChildren && grandChildren.length > 0) {
    return findLastDescendant(lastChild.id, childrenMap, allNodes);
  }
  return lastChild.id;
}

function ThinkingFlow() {
  const { t } = useTranslation("thinking");
  const { t: tc } = useTranslation("common");
  const {
    enabled,
    connected,
    sessions,
    activeSessionId,
    activeSession,
    selectedNodeId,
    autoFollow,
    _statusSynced,
    setEnabled,
    setSessions,
    setActiveSessionId,
    setActiveSession,
    setSelectedNodeId,
    setAutoFollow,
    setStatusSynced,
    startSSE,
    stopSSE,
  } = useThinkingStore();

  const prevNodeCount = useRef(0);
  const { setCenter, fitView, getZoom } = useReactFlow();
  const [showTools, setShowTools] = useState(true);
  const [showSessions, setShowSessions] = useState(false);
  const isMobile = useIsMobile();

  // 服务端 enabled 状态只同步一次（首次加载），切换页面不重置用户的开关选择
  useEffect(() => {
    if (!_statusSynced) {
      thinkingApi.status().then((r) => {
        setEnabled(r.data.enabled);
        if (r.data.enabled) {
          startSSE();
        }
        setStatusSynced(true);
      }).catch((e) => console.warn("[API]", e));
    }
  }, [_statusSynced, setEnabled, setStatusSynced, startSSE]);

  // 每次进入页面都刷新会话列表（获取离开期间产生的新会话）
  useEffect(() => {
    thinkingApi.sessions(50).then((r) => {
      setSessions(r.data.sessions ?? []);
    }).catch((e) => console.warn("[API]", e));
  }, [setSessions]);

  const handleToggle = useCallback(() => {
    const next = !enabled;
    thinkingApi.toggle(next).then(() => {
      setEnabled(next);
      if (next) {
        startSSE();
      } else {
        stopSSE();
      }
    }).catch((e) => console.warn("[API]", e));
  }, [enabled, setEnabled, startSSE, stopSSE]);

  const handleSelectSession = useCallback((id: string) => {
    setActiveSessionId(id);
    setSelectedNodeId(null);
    prevNodeCount.current = 0;
    const local = sessions.find((s) => s.id === id);
    if (local && activeSession?.id !== id) {
      thinkingApi.session(id).then((r) => {
        if (r.data && !r.data.error) {
          setActiveSession(r.data);
        }
      }).catch((e) => console.warn("[API]", e));
    }
  }, [sessions, activeSession, setActiveSessionId, setActiveSession, setSelectedNodeId]);

  const { nodes: flowNodes, edges: flowEdges } = useMemo(
    () => buildFlowElements(activeSession),
    [activeSession],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges);

  useEffect(() => {
    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [flowNodes, flowEdges, setNodes, setEdges]);

  useEffect(() => {
    if (!autoFollow || nodes.length === 0) return;

    if (prevNodeCount.current === 0) {
      prevNodeCount.current = nodes.length;
      const timer = setTimeout(() => fitView({ padding: 0.3, duration: 300 }), 50);
      return () => clearTimeout(timer);
    }

    if (nodes.length > prevNodeCount.current) {
      prevNodeCount.current = nodes.length;
      const last = nodes[nodes.length - 1];
      if (last) {
        const timer = setTimeout(() => {
          const zoom = Math.max(getZoom(), 0.6);
          setCenter(last.position.x + 110, last.position.y + 30, { zoom, duration: 250 });
        }, 50);
        return () => clearTimeout(timer);
      }
    }
    prevNodeCount.current = nodes.length;
  }, [nodes.length, autoFollow, fitView, setCenter, getZoom]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNodeId(node.id);
  }, [setSelectedNodeId]);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId || !activeSession) return null;
    return activeSession.nodes.find((n) => n.id === selectedNodeId) ?? null;
  }, [selectedNodeId, activeSession]);

  const availableTools = activeSession?.available_tools ?? [];

  return (
    <div className="flex h-full gap-0">
      {/* 左侧：会话列表（桌面常驻，移动端抽屉） */}
      {(!isMobile || showSessions) && (
        <>
          {isMobile && (
            <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setShowSessions(false)} />
          )}
          <div className={cn(
            "border-r border-border bg-panel flex flex-col",
            isMobile ? "fixed inset-y-0 left-0 z-50 w-64" : "w-56 shrink-0",
          )}>
            <div className="px-4 py-3 border-b border-border">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-heading uppercase tracking-wider">
                  {t("sessionList")}
                </span>
                <button
                  onClick={() => {
                    thinkingApi.sessions(50).then((r) => setSessions(r.data.sessions ?? [])).catch((e) => console.warn("[API]", e));
                  }}
                  className="p-1 rounded-sm text-muted hover:text-foreground hover:bg-hover"
                  title={t("refresh")}
                >
                  <RefreshCw size={12} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              <SessionList
                sessions={sessions}
                activeId={activeSessionId}
                onSelect={(id) => {
                  handleSelectSession(id);
                  if (isMobile) setShowSessions(false);
                }}
              />
            </div>
          </div>
        </>
      )}

      {/* 中间：流图画布 */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 工具栏 */}
        <div className="flex items-center gap-2 px-3 md:px-4 py-2 border-b border-border bg-panel">
          {isMobile && (
            <button
              onClick={() => setShowSessions(true)}
              className="flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium text-muted hover:text-foreground transition-all"
              title={t("sessionList")}
            >
              <List size={14} />
            </button>
          )}
          <button
            onClick={handleToggle}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all",
              enabled
                ? "bg-ok-subtle text-ok border border-ok"
                : "bg-hover text-muted border border-border hover:border-border-strong",
            )}
          >
            {enabled ? <Power size={12} /> : <PowerOff size={12} />}
            {enabled ? t("tracking") : t("disabled")}
          </button>

          <div className="flex items-center gap-1 text-[10px] text-muted">
            <div
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                connected ? "bg-ok" : "bg-danger",
              )}
            />
            {connected ? tc("connected") : tc("disconnected")}
          </div>

          <div className="flex-1" />

          <button
            onClick={() => setShowTools(!showTools)}
            className={cn(
              "hidden md:flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium transition-all",
              showTools
                ? "bg-accent-subtle text-accent"
                : "text-muted hover:text-foreground",
            )}
            title={t("toolsPanel")}
          >
            {showTools ? <PanelLeftClose size={11} /> : <PanelLeftOpen size={11} />}
            {t("toolsPanel")}
          </button>

          <button
            onClick={() => setAutoFollow(!autoFollow)}
            className={cn(
              "flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium transition-all",
              autoFollow
                ? "bg-accent-subtle text-accent"
                : "text-muted hover:text-foreground",
            )}
            title={t("autoFollow")}
          >
            <Crosshair size={11} />
            <span className="hidden sm:inline">{t("autoFollow")}</span>
          </button>

          {activeSession && (
            <div className="text-[10px] text-muted font-mono">
              {t("nNodes", { count: activeSession.nodes.length })}
            </div>
          )}
        </div>

        {/* 主区域：工具面板 + 画布 */}
        <div className="flex-1 flex min-h-0">
          {/* 工具面板（仅桌面端） */}
          {showTools && !isMobile && (
            <div className="w-56 shrink-0 border-r border-border bg-panel">
              <ToolsPanel tools={availableTools} />
            </div>
          )}

          <div className="flex-1 relative">
            {!activeSession ? (
              <div className="flex items-center justify-center h-full text-sm text-muted px-4 text-center">
                {enabled
                  ? t("waitingForActivity")
                  : t("enableTracking")}
              </div>
            ) : (
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={onNodeClick}
                nodeTypes={NODE_TYPES}
                fitView
                fitViewOptions={{ padding: 0.3 }}
                minZoom={0.2}
                maxZoom={2}
                proOptions={{ hideAttribution: true }}
                className="thinking-flow"
              >
                <Background
                  variant={BackgroundVariant.Dots}
                  gap={20}
                  size={1}
                  color="var(--border)"
                />
                <Controls
                  showInteractive={false}
                  className="!bg-card !border-border !shadow-sm [&>button]:!bg-card [&>button]:!border-border [&>button]:!fill-[var(--text)] [&>button:hover]:!bg-hover"
                />
                <MiniMap
                  nodeColor={() => "var(--accent)"}
                  maskColor="rgba(0,0,0,0.6)"
                  className="!bg-card !border-border !hidden md:!block"
                />
              </ReactFlow>
            )}
          </div>
        </div>
      </div>

      {/* 右侧：节点详情（桌面常驻，移动端全屏抽屉） */}
      {selectedNode && (
        <div className={cn(
          "border-l border-border bg-panel",
          isMobile ? "fixed inset-y-0 right-0 z-50 w-full max-w-sm shadow-lg" : "w-72 shrink-0",
        )}>
          <NodeDetail
            node={selectedNode}
            onClose={() => setSelectedNodeId(null)}
          />
        </div>
      )}
    </div>
  );
}

export default function Thinking() {
  const { t } = useTranslation("thinking");
  return (
    <div className="h-full flex flex-col">
      <div className="px-3 md:px-6 py-4 border-b border-border">
        <h1 className="text-lg font-semibold text-heading">{t("title")}</h1>
        <p className="text-xs text-muted mt-0.5">
          {t("subtitle")}
        </p>
      </div>
      <div className="flex-1 min-h-0">
        <ReactFlowProvider>
          <ThinkingFlow />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
