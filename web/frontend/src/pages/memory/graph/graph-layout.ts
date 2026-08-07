/** 关系图谱力导向布局（d3-force 静态模拟 → xyflow 坐标）。 */

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
} from "d3-force";
import type { GraphData } from "@/lib/types";

export interface LayoutPoint {
  x: number;
  y: number;
}

interface SimNode {
  id: number;
  degree: number;
  x?: number;
  y?: number;
}

/** 对全图跑静态力导向模拟，返回节点 id → 坐标。 */
export function layoutGraph(data: GraphData): Map<number, LayoutPoint> {
  const result = new Map<number, LayoutPoint>();
  if (data.nodes.length === 0) return result;

  const degrees = new Map<number, number>();
  for (const edge of data.edges) {
    degrees.set(edge.subject.id, (degrees.get(edge.subject.id) ?? 0) + 1);
    degrees.set(edge.object.id, (degrees.get(edge.object.id) ?? 0) + 1);
  }
  const simNodes: SimNode[] = data.nodes.map((n) => ({
    id: n.id,
    degree: degrees.get(n.id) ?? 0,
  }));
  const nodeIds = new Set(simNodes.map((n) => n.id));
  const simLinks = data.edges
    .filter((e) => nodeIds.has(e.subject.id) && nodeIds.has(e.object.id))
    .map((e) => ({ source: e.subject.id, target: e.object.id }));

  const simulation = forceSimulation(simNodes)
    .force("charge", forceManyBody().strength(-320))
    .force("link", forceLink(simLinks).id((d) => (d as SimNode).id).distance(150))
    .force("collide", forceCollide(70))
    .force("center", forceCenter(0, 0))
    .stop();

  // 静态布局：同步跑固定轮数（节点越多收敛轮数越多，封顶防大图卡顿）
  const ticks = Math.min(600, 200 + data.nodes.length * 3);
  for (let i = 0; i < ticks; i += 1) simulation.tick();

  for (const node of simNodes) {
    result.set(node.id, { x: node.x ?? 0, y: node.y ?? 0 });
  }
  return result;
}
