import { useContext } from "react";
import type { DragPreviewProps } from "react-arborist";
import { FolderClosed } from "lucide-react";
import { useFileTreeStore } from "./file-tree-store";
import { findNode, fileIcon } from "./file-tree-utils";
import { FileTreeContext } from "./FileTreeNode";

/**
 * 拖拽预览浮层：跟随鼠标显示被拖文件名（多选时附数量角标）。
 * 库默认把原生拖拽图像设为空白（drag 预览交给 React 层渲染），
 * 不提供自定义预览时拖动完全无视觉反馈，像"没拖起来"。
 */
export function FileTreeDragPreview({ offset, id, dragIds, isDragging }: DragPreviewProps) {
  const ctx = useContext(FileTreeContext);
  if (!isDragging || !offset || !id || !ctx) return null;
  const node = findNode(useFileTreeStore.getState().trees[ctx.root].children ?? [], id);
  if (!node) return null;
  const { Icon, className } = node.type === "dir"
    ? { Icon: FolderClosed, className: "text-muted" }
    : fileIcon(node.name);
  return (
    <div className="fixed inset-0 pointer-events-none z-[130]">
      <div
        className="absolute"
        style={{ transform: `translate(${offset.x + 10}px, ${offset.y + 6}px)` }}
      >
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-border bg-card shadow-lg text-xs text-foreground max-w-56">
          <Icon size={13} className={`shrink-0 ${className}`} />
          <span className="truncate">{node.name}</span>
          {dragIds.length > 1 && (
            <span className="ml-1 px-1 rounded bg-accent-subtle text-accent text-[10px] shrink-0">
              {dragIds.length}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
