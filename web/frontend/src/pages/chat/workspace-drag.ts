/** 工作区文件拖拽的共享载体 — 不依赖原生 dataTransfer（真人拖放下 React 事件委托
 * 与 arborist 的拖拽预处理会让 dragstart 里的 setData 时机不确定，payload 读不到）。
 *
 * 方案：拖出端把 payload 写进模块级变量，落下端直接读这个变量。
 * dataTransfer 仅用于「这次拖拽是否来自工作区文件树」的标记判定（types 含 MIME）。
 */

import type { WorkspaceRoot } from "@/lib/types";

export interface WorkspaceDragPayload {
  path: string;
  name: string;
  root: WorkspaceRoot;
  is_dir: boolean;
}

export const WORKSPACE_FILE_MIME = "application/x-workspace-file";

let _payload: WorkspaceDragPayload | null = null;

export function setWorkspaceDragPayload(p: WorkspaceDragPayload): void {
  _payload = p;
}

/** 落下端消费 payload（读一次即清空，防陈旧复用） */
export function consumeWorkspaceDragPayload(): WorkspaceDragPayload | null {
  const p = _payload;
  _payload = null;
  return p;
}

/** 拖拽中（dragenter/dragover）判定：payload 在手即认为是工作区文件树拖拽。
 * 不再依赖 dataTransfer.types——真人拖放下 types 内容因浏览器/React 委托而异，
 * payload 模块变量才是唯一可靠信号。
 */
export function hasWorkspaceFileDrag(_dt?: DataTransfer | null): boolean {
  return _payload !== null;
}
