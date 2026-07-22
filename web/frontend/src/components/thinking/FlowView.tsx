import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  ReactFlow,
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
import type { ThinkingSession } from "@/stores/thinking-store";
import { buildFlowElements } from "./flow-layout";
import TraceNodeComponent from "./TraceNode";

const NODE_TYPES: NodeTypes = {
  trace: TraceNodeComponent,
};

interface Props {
  session: ThinkingSession;
  autoFollow: boolean;
  onNodeClick: (nodeId: string) => void;
}

/** 流程图视图：ReactFlow 画布 + 自动跟随最新节点 */
export function FlowView({ session, autoFollow, onNodeClick }: Props) {
  const prevNodeCount = useRef(0);
  const { setCenter, fitView, getZoom } = useReactFlow();

  const { nodes: flowNodes, edges: flowEdges } = useMemo(
    () => buildFlowElements(session),
    [session],
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

  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    onNodeClick(node.id);
  }, [onNodeClick]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleNodeClick}
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
  );
}
