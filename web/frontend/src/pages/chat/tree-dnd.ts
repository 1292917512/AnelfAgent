/** 文件树拖拽的 react-dnd 桥 — 树与对话区共享同一 manager 时的落点数据。

 * react-dnd 的 HTML5 backend 在「没有注册 drop target 命中」时给禁止光标并
 * cancel 浏览器默认 drop，所以对话区此前用 DOM drop 监听永远收不到
 * （树内移动却是好的，因为它是注册 target）。解法：对话区经 useDrop 注册
 * 为与树同 type 的 target；根目录归属由 FileTree 挂载时登记到模块级变量。
 */

import { createDragDropManager } from "dnd-core";
import { HTML5Backend } from "react-dnd-html5-backend";
import type { WorkspaceRoot } from "@/lib/types";

/** 树与对话区共享的 DragDropManager（单例）：Tree 经 dndManager prop 消费，
 * ChatDropZone 经 DndProvider manager prop 消费——同一 manager 下 useDrag
 * 的 item 才能被 useDrop 的 accept 命中。
 */
export const sharedDndManager = createDragDropManager(HTML5Backend);

/** 树节点的 react-dnd dragType（arborist 默认 "NODE"） */
export const TREE_NODE_DRAG_TYPE = "NODE";

/** FileTree 挂载时登记的当前根目录（树是单实例面板，同时只有一个根在活动） */
let _activeRoot: WorkspaceRoot = "workspace";
export function registerTreeRoot(root: WorkspaceRoot): void {
  _activeRoot = root;
}
export function activeTreeRoot(): WorkspaceRoot {
  return _activeRoot;
}

/** 树节点 drag item 的 data 段（arborist drag-hook item() 产出 node.data） */
export interface TreeNodeDragItem {
  id: string;
  dragIds: string[];
  data: {
    name: string;
    path: string;
    type: "dir" | "file";
  };
}
