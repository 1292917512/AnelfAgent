/** 力导向布局 Web Worker：大图模拟移出主线程（d3 官方推荐的大图模式）。 */

import { simulateLayout } from "./layout-core";

export interface LayoutRequest {
  requestId: number;
  nodes: { id: number; degree: number }[];
  links: { source: number; target: number }[];
}

export interface LayoutResponse {
  requestId: number;
  positions: [number, { x: number; y: number }][];
}

const ctx = self as unknown as {
  onmessage: ((ev: MessageEvent<LayoutRequest>) => void) | null;
  postMessage: (message: LayoutResponse) => void;
};

ctx.onmessage = (ev: MessageEvent<LayoutRequest>) => {
  const { requestId, nodes, links } = ev.data;
  const positions = simulateLayout(nodes, links);
  ctx.postMessage({ requestId, positions: Array.from(positions.entries()) });
};
