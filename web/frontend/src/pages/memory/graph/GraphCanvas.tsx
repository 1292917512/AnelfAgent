/** 关系图谱画布：GraphData → xyflow 节点/边，递进式双段布局。
 *
 * 第一段（同步，<1ms）：径向初布局即刻渲染可交互；
 * 第二段：d3-force 精算——大图（>80 节点）在 Web Worker 跑（d3 官方
 * 大图模式），小图延一帧主线程精算；完成后节点归位，主线程不再被
 * 模拟阻塞，切页即见。worker 不可用的极端环境同步回退。
 */

import { useEffect, useMemo, useRef } from "react";
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
import { layoutInputs } from "./graph-layout";
import {
  radialLayout as radial,
  simulateLayout,
  type LayoutPoint,
} from "./layout-core";
import { GraphNodeCard, type GraphFlowNode } from "./GraphNodeCard";

const NODE_TYPES: NodeTypes = { graphNode: GraphNodeCard };

/** 小图阈值：低于此数延一帧主线程精算更快（免去 worker 往返）。 */
const SYNC_LAYOUT_THRESHOLD = 80;

interface Props {
  data: GraphData;
  selectedNodeId: number | null;
  onNodeClick: (nodeId: number) => void;
  onEdgeClick: (edgeId: number) => void;
}

export function GraphCanvas({ data, selectedNodeId, onNodeClick, onEdgeClick }: Props) {
  const { t } = useTranslation("graph");
  const requestIdRef = useRef(0);

  // 布局输入一次计算：度数（节点卡展示 + 模拟输入）与边列表共用；
  // 第一段径向初布局在此完成（廉价同步），不含力模拟
  const { flowNodes, flowEdges, layoutNodes, links } = useMemo(() => {
    const { nodes: layoutNodes, links } = layoutInputs(data);
    const degrees = new Map(layoutNodes.map((n) => [n.id, n.degree]));
    const positions = radial(layoutNodes);
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
    return { flowNodes, flowEdges, layoutNodes, links };
  }, [data, t]);

  const [nodes, setNodes, onNodesChange] = useNodesState<GraphFlowNode>(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(flowEdges);

  useEffect(() => {
    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [flowNodes, flowEdges, setNodes, setEdges]);

  // 第二段：力导向精算（异步）——数据变更以 requestId 失效在途结果
  useEffect(() => {
    const requestId = ++requestIdRef.current;
    if (layoutNodes.length <= SYNC_LAYOUT_THRESHOLD) {
      // 小图延一帧：径向初布局先上屏，再同步精算归位
      const raf = requestAnimationFrame(() => {
        if (requestId !== requestIdRef.current) return;
        refine(simulateLayout(layoutNodes, links), setNodes);
      });
      return () => cancelAnimationFrame(raf);
    }
    let worker: Worker;
    try {
      worker = new Worker(new URL("./graph-layout.worker.ts", import.meta.url), {
        type: "module",
      });
    } catch {
      // worker 不可用（极端环境）：同步回退，保持旧路径行为
      refine(simulateLayout(layoutNodes, links), setNodes);
      return;
    }
    worker.onmessage = (
      ev: MessageEvent<{ requestId: number; positions: [number, LayoutPoint][] }>,
    ) => {
      if (ev.data.requestId !== requestIdRef.current) return; // 数据已变更，丢弃过期结果
      refine(new Map(ev.data.positions), setNodes);
    };
    worker.postMessage({ requestId, nodes: layoutNodes, links });
    return () => worker.terminate();
  }, [layoutNodes, links, setNodes]);

  // 选中态经状态更新打标（替代逐渲染全量 remap——拖拽/缩放不再重建全部节点对象）
  useEffect(() => {
    setNodes((prev) => {
      let changed = false;
      const next = prev.map((n) => {
        const selected = String(selectedNodeId ?? "") === n.id;
        if (n.selected !== selected) {
          changed = true;
          return { ...n, selected };
        }
        return n;
      });
      return changed ? next : prev;
    });
  }, [selectedNodeId, setNodes]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onNodeClick(Number(node.id))}
      onEdgeClick={(_, edge) => onEdgeClick(Number(edge.id))}
      nodeTypes={NODE_TYPES}
      onlyRenderVisibleElements
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

/** 把精算坐标应用到现有节点（保留 data/selected，仅动 position）。 */
function refine(
  positions: Map<number, LayoutPoint>,
  setNodes: React.Dispatch<React.SetStateAction<GraphFlowNode[]>>,
) {
  if (positions.size === 0) return;
  setNodes((prev) =>
    prev.map((n) => {
      const pos = positions.get(Number(n.id));
      return pos ? { ...n, position: pos } : n;
    }),
  );
}
