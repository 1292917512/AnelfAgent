/** 工作区文件拖拽的共享载体。
 *
 * 树的拖拽引擎是 react-dnd（arborist 内置，dragHandle ref 是其 connector），
 * 树内移动由它全权处理；「拖到对话区引用」是另一条独立链路——在 document
 * capture 阶段监听一次 dragstart，从拖起行的 data-ws-* 属性读出 payload
 * 存入模块变量，dragend 清空；对话区在 drop 时消费。全程不碰
 * dataTransfer（react-dnd 自管，且真人拖放下读写时机不可控）。
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

/** 落下端消费 payload（读一次即清空，防陈旧复用） */
export function consumeWorkspaceDragPayload(): WorkspaceDragPayload | null {
  const p = _payload;
  _payload = null;
  return p;
}

/** 拖拽进行中（dragenter/dragover 判定遮罩与放行） */
export function hasWorkspaceFileDrag(): boolean {
  return _payload !== null;
}

/** 非破坏性查看当前 payload（仅诊断展示用；消费一律走 consume） */
export function peekWorkspaceDragPayload(): WorkspaceDragPayload | null {
  return _payload;
}

/** 行元素携带的拖拽属性（dragstart 时由监听器读取） */
export function dragDataAttrs(payload: WorkspaceDragPayload) {
  return {
    "data-ws-path": payload.path,
    "data-ws-name": payload.name,
    "data-ws-root": payload.root,
    "data-ws-dir": payload.is_dir ? "1" : "",
  };
}

/** 注册 document 级 capture 监听（幂等，FileTree 挂载时调用一次）。 */
export function initWorkspaceDragSource(): void {
  if (initWorkspaceDragSource._done) return;
  initWorkspaceDragSource._done = true;

  document.addEventListener(
    "dragstart",
    (e) => {
      const el = (e.target as HTMLElement | null)?.closest?.("[data-ws-path]");
      if (!el) return;
      const path = el.getAttribute("data-ws-path") || "";
      if (!path) return;
      _payload = {
        path,
        name: el.getAttribute("data-ws-name") || path,
        root: (el.getAttribute("data-ws-root") === "project" ? "project" : "workspace"),
        is_dir: el.getAttribute("data-ws-dir") === "1",
      };
    },
    true,
  );
  // 拖拽结束兜底清空（drop 未被任何区消费时防 payload 残留）
  document.addEventListener(
    "dragend",
    () => {
      _payload = null;
    },
    true,
  );
}
initWorkspaceDragSource._done = false;
