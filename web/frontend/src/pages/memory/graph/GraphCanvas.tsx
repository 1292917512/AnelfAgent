/** 关系图谱画布：GraphData → xyflow 节点/边 + 力导向布局。 */

import { useEffect, useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type NodeTypes,
} from "@xyflow/react";
import { useTranslation } from "react-i18next";
import type { GraphData } from "@/lib/types";
import { layoutGraph } from "./graph-layout";
import { GraphNodeCard, type GraphFlowNode } from "./GraphNodeCard";

const NODE_TYPES: NodeTypes = { graphNode: GraphNodeCard };

interface Props {
  data: GraphData;
  selectedNodeId: number | null;
  onNodeClick: (nodeId: number) => void;
  onEdgeClick: (edgeId: number) => void;
}

export function GraphCanvas({ data, selectedNodeId, onNodeClick, onEdgeClick }: Props) {
  const { t } = useTranslation("graph");

  const { flowNodes, flowEdges } = useMemo(() => {
    const positions = layoutGraph(data);
    const degrees = new Map<number, number>();
    for (const edge of data.edges) {
      degrees.set(edge.subject.id, (degrees.get(edge.subject.id) ?? 0) + 1);
      degrees.set(edge.object.id, (degrees.get(edge.object.id) ?? 0) + 1);
    }
    const flowNodes: GraphFlowNode[] = data.nodes.map((node) => ({
      id: String(node.id),
      type: "graphNode",
      position: positions.get(node.id) ?? { x: 0, y: 0 },
      data: {
        label: node.label || node.node_key,
        nodeKey: node.node_key,
        nodeType: node.node_type,
        degree: degrees.get(node.id) ?? 0,
        typeLabel: t(`nodeTypes.${node.node_type}`, { defaultValue: node.node_type }),
      },
    }));
    const flowEdges: Edge[] = data.edges.map((edge) => ({
      id: String(edge.id),
      source: String(edge.subject.id),
      target: String(edge.object.id),
      label: `${edge.predicate} ${edge.strength.toFixed(2)}`,
      animated: false,
      style: {
        stroke: "var(--muted)",
        strokeWidth: 0.8 + edge.strength * 2.2,
        ...(edge.origin === "heartbeat_extract" ? { strokeDasharray: "6 4" } : {}),
      },
      labelStyle: { fill: "var(--text)", fontSize: 11 },
      labelBgStyle: { fill: "var(--card)", fillOpacity: 0.9 },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 3,
      ...(edge.symmetric
        ? {}
        : { markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted)" } }),
    }));
    return { flowNodes, flowEdges };
  }, [data, t]);

  const [nodes, setNodes, onNodesChange] = useNodesState<GraphFlowNode>(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(flowEdges);

  useEffect(() => {
    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [flowNodes, flowEdges, setNodes, setEdges]);

  return (
    <ReactFlow
      nodes={nodes.map((n) => ({ ...n, selected: String(selectedNodeId ?? "") === n.id }))}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onNodeClick(Number(node.id))}
      onEdgeClick={(_, edge) => onEdgeClick(Number(edge.id))}
      nodeTypes={NODE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.15}
      maxZoom={2.5}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--border)" />
      <Controls showInteractive={false} />
      <MiniMap pannable zoomable className="!bg-card" nodeColor="var(--accent)" />
    </ReactFlow>
  );
}
