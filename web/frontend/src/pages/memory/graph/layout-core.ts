/** 力导向布局核心（纯函数）：主线程回退与 Web Worker 共用，无 DOM 依赖。 */

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
} from "d3-force";

export interface LayoutPoint {
  x: number;
  y: number;
}

export interface LayoutNode {
  id: number;
  degree: number;
}

export interface LayoutLink {
  source: number;
  target: number;
}

/** 全图静态力导向模拟（同步，重计算——大图应经 worker 调用）。 */
export function simulateLayout(
  nodes: LayoutNode[],
  links: LayoutLink[],
): Map<number, LayoutPoint> {
  const result = new Map<number, LayoutPoint>();
  if (nodes.length === 0) return result;

  // 模拟节点：d3-force 原地写入 x/y（收敛坐标）
  interface SimNode {
    id: number;
    degree: number;
    x?: number;
    y?: number;
  }
  const simNodes: SimNode[] = nodes.map((n) => ({ ...n }));
  const nodeIds = new Set(nodes.map((n) => n.id));
  const simLinks = links.filter(
    (l) => nodeIds.has(l.source) && nodeIds.has(l.target),
  );

  const simulation = forceSimulation(simNodes)
    .force("charge", forceManyBody().strength(-320))
    .force("link", forceLink(simLinks).id((d) => (d as SimNode).id).distance(150))
    .force("collide", forceCollide(70))
    .force("center", forceCenter(0, 0))
    .stop();

  // 静态布局：同步跑固定轮数（节点越多收敛轮数越多，封顶防大图卡顿）
  const ticks = Math.min(600, 200 + nodes.length * 3);
  for (let i = 0; i < ticks; i += 1) simulation.tick();

  for (const node of simNodes) {
    result.set(node.id, { x: node.x ?? 0, y: node.y ?? 0 });
  }
  return result;
}

/**
 * 径向初布局（O(n log n)）：高度数节点居内环、低度数外环，黄金角均匀分布。
 * 递进式显示的第一段——数据到达即刻可交互的确定性占位坐标，力导向精算
 * 由 worker 异步完成后归位。节点少于 4 个时直接一行排开（避免退化重叠）。
 */
export function radialLayout(nodes: LayoutNode[]): Map<number, LayoutPoint> {
  const result = new Map<number, LayoutPoint>();
  if (nodes.length === 0) return result;
  if (nodes.length < 4) {
    nodes.forEach((n, i) => result.set(n.id, { x: i * 180, y: 0 }));
    return result;
  }
  const sorted = [...nodes].sort((a, b) => b.degree - a.degree);
  const ringCapacity = Math.max(12, Math.ceil(Math.sqrt(sorted.length) * 2));
  let ring = 0;
  let ringIndex = 0;
  // 环半径按容量递增；黄金角打散避免轴线对齐的视觉聚簇
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (const node of sorted) {
    const radius = ring === 0 ? 0 : ring * 160;
    const angle = ringIndex * goldenAngle + ring * 0.7;
    result.set(node.id, {
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
    });
    ringIndex += 1;
    if (ringIndex >= ringCapacity + ring * 4) {
      ring += 1;
      ringIndex = 0;
    }
  }
  return result;
}
