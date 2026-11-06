import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type NodeTypes,
  BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { ThinkingSession } from "@/lib/types";
import { buildFlowElements } from "./flow-layout";
import TraceNodeComponent, { type TraceGraphNode } from "./TraceNode";
import { useMediaQuery } from "@/lib/use-media-query";

const NODE_TYPES: NodeTypes = {
  trace: TraceNodeComponent,
};

interface Props {
  session: ThinkingSession;
  autoFollow: boolean;
  onNodeClick: (nodeId: string) => void;
}

export const FlowView = memo(function FlowView(props: Props) {
  return <ReactFlowProvider><FlowCanvas {...props} /></ReactFlowProvider>;
});

/** 流程图视图：ReactFlow 画布 + 自动跟随最新节点；叠加 plan 虚拟节点 */
function FlowCanvas({ session, autoFollow, onNodeClick }: Props) {
  const prevNodeCount = useRef(0);
  const { setCenter, getZoom } = useReactFlow();

  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [layout, setLayout] = useState(() => buildFlowElements(session));
  if (layout.source !== session.nodes) setLayout(buildFlowElements(session, layout));
  const { nodes: flowNodes, edges: flowEdges } = layout;
  const [nodes, setNodes, onNodesChange] = useNodesState<TraceGraphNode>(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges);
  const previousLayout = useRef(flowNodes);
  useEffect(() => {
    const oldLayout = new Map(previousLayout.current.map((node) => [node.id, node]));
    setNodes((current) => {
      const existing = new Map(current.map((node) => [node.id, node]));
      return flowNodes.map((node) => {
        const old = existing.get(node.id);
        const original = oldLayout.get(node.id);
        if (!old || !original || original.position.x !== node.position.x || original.position.y !== node.position.y) return node;
        return old.data === node.data ? old : { ...old, data: node.data };
      });
    });
    previousLayout.current = flowNodes;
  }, [flowNodes, setNodes]);
  useEffect(() => { setEdges(flowEdges); }, [flowEdges, setEdges]);
  const last = nodes[nodes.length - 1];
  const lastX = last?.position.x ?? 0;
  const lastY = last?.position.y ?? 0;
  useEffect(() => {
    if (!autoFollow || session.ended || nodes.length === 0) return;

    if (prevNodeCount.current === 0) {
      prevNodeCount.current = nodes.length;
      return;
    }

    if (nodes.length > prevNodeCount.current) {
      prevNodeCount.current = nodes.length;
      const zoom = Math.max(getZoom(), 0.6);
      void setCenter(lastX + 110, lastY + 30, { zoom, duration: reducedMotion ? 0 : 250 });
    }
    prevNodeCount.current = nodes.length;
  }, [nodes.length, lastX, lastY, autoFollow, session.ended, setCenter, getZoom, reducedMotion]);

  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    onNodeClick(node.id);
  }, [onNodeClick]);

  const fitOptions = useMemo(() => ({ padding: .25, maxZoom: 1, nodes: autoFollow && !session.ended ? flowNodes.slice(-3) : flowNodes.slice(0, 3) }), [autoFollow, session.ended, flowNodes]);
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleNodeClick}
      nodeTypes={NODE_TYPES}
      fitView
      fitViewOptions={fitOptions}
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
  );
}
