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

/** 拖拽中（dragenter/dragover）判定：types 含工作区 MIME 或 payload 已在手 */
export function hasWorkspaceFileDrag(dt: DataTransfer | null): boolean {
  if (_payload !== null) return true;
  if (!dt) return false;
  return Array.from(dt.types).includes(WORKSPACE_FILE_MIME);
}
