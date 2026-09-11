/** 关系图谱布局门面：径向初布局（即时）+ 力导向精算（同步回退）。

主路径是 GraphCanvas 的 worker 异步精算（layout-core + graph-layout.worker）；
本模块保留两个同步入口——径向初布局供递进式第一段，simulateLayout 作为
worker 不可用时的回退（小图直接主线程算，开销可接受）。
 */

import {
  radialLayout as radial,
  simulateLayout,
  type LayoutLink,
  type LayoutNode,
  type LayoutPoint,
} from "./layout-core";
import type { GraphData } from "@/lib/types";

export type { LayoutPoint };

function layoutInputs(data: GraphData): {
  nodes: LayoutNode[];
  links: LayoutLink[];
} {
  const degrees = new Map<number, number>();
  for (const edge of data.edges) {
    degrees.set(edge.subject.id, (degrees.get(edge.subject.id) ?? 0) + 1);
    degrees.set(edge.object.id, (degrees.get(edge.object.id) ?? 0) + 1);
  }
  return {
    nodes: data.nodes.map((n) => ({ id: n.id, degree: degrees.get(n.id) ?? 0 })),
    links: data.edges.map((e) => ({ source: e.subject.id, target: e.object.id })),
  };
}

/** 径向初布局：数据到达即刻可交互的占位坐标（O(n log n)，主线程安全）。 */
export function radialLayout(data: GraphData): Map<number, LayoutPoint> {
  return radial(layoutInputs(data).nodes);
}

/** 力导向精算（同步，重计算）：worker 不可用时的回退路径。 */
export function layoutGraph(data: GraphData): Map<number, LayoutPoint> {
  const { nodes, links } = layoutInputs(data);
  return simulateLayout(nodes, links);
}

export { layoutInputs };
