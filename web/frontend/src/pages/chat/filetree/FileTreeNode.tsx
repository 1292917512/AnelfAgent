import { createContext, useContext, useEffect, useRef, type MouseEvent } from "react";
import type { NodeRendererProps, RowRendererProps } from "react-arborist";
import { ChevronDown, ChevronRight, FolderClosed, FolderOpen, Loader2, MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/lib/use-media-query";
import { workspaceMediaKind, isPreviewableBinary, type WorkspaceNode, type WorkspaceRoot } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useFileTreeStore } from "./file-tree-store";
import { fileIcon } from "./file-tree-utils";
import { WORKSPACE_FILE_MIME } from "../ChatInput";

/** FileTree 向节点行下发的上下文（react-arborist 行渲染器不支持自定义 props） */
export interface FileTreeContextValue {
  root: WorkspaceRoot;
  onContextMenu: (e: MouseEvent, node: WorkspaceNode) => void;
}

export const FileTreeContext = createContext<FileTreeContextValue | null>(null);

function useFileTreeContext(): FileTreeContextValue {
  const ctx = useContext(FileTreeContext);
  if (!ctx) throw new Error("FileTreeNode 必须在 FileTreeContext 内渲染");
  return ctx;
}

/** 重命名内联输入框：Enter/失焦提交，Esc 取消（文件名预选主名部分） */
function RenameInput({ node }: { node: NodeRendererProps<WorkspaceNode>["node"] }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const dot = node.data.name.lastIndexOf(".");
    el.setSelectionRange(0, dot > 0 && node.data.type === "file" ? dot : node.data.name.length);
  }, [node]);
  return (
    <input
      ref={ref}
      defaultValue={node.data.name}
      className="flex-1 min-w-0 px-1 py-0 text-xs bg-card border border-accent rounded outline-none"
      onBlur={(e) => node.submit(e.currentTarget.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") node.submit(e.currentTarget.value);
        if (e.key === "Escape") node.reset();
      }}
      onClick={(e) => e.stopPropagation()}
    />
  );
}

/**
 * 自定义行容器（renderRow）。库默认行在外层 div 绑定了 `node.handleClick`
 * （click → select + activate），与行内 onClick 叠加会导致一次点击双触发
 * （目录开即关）；这里不绑定 click，行交互全部由内层内容元素负责。
 */
export function FileTreeRow({ innerRef, attrs, children }: RowRendererProps<WorkspaceNode>) {
  return (
    <div {...attrs} ref={innerRef} onFocus={(e) => e.stopPropagation()}>
      {children}
    </div>
  );
}

/** 文件树节点行（react-arborist 虚拟化渲染）：图标 / 重命名编辑 / 选择高亮 / 拖放 */
export function FileTreeNode(props: NodeRendererProps<WorkspaceNode>) {
  const { node, style, dragHandle } = props;
  const { root, onContextMenu } = useFileTreeContext();
  const isMobile = useIsMobile();
  const openFiles = useWorkbenchStore((s) => s.openFiles);
  const openFilePath = useWorkbenchStore((s) => s.openFilePath);
  const openFile = useWorkbenchStore((s) => s.openFile);
  const loading = useFileTreeStore((s) => s.loadingDirs[`${root}:${node.data.path}`] === true);

  const data = node.data;
  const isDir = data.type === "dir";
  const isActive = openFilePath === data.path;
  const isOpened = !isActive && openFiles.includes(data.path);
  // 二进制中的图片/音视频/PDF/DOCX/XLSX 可打开预览，其余二进制不可编辑
  const openable = isDir || !data.binary || workspaceMediaKind(data.name) !== null || isPreviewableBinary(data.name);
  const { Icon, className: iconClass } = fileIcon(data.name);

  const handleClick = () => {
    node.select();
    if (isDir) {
      node.toggle();
    } else if (openable) {
      openFile(data.path, root);
      // 移动端打开文件后收起抽屉，直接进入编辑器（仅在抽屉打开时收起）
      const wb = useWorkbenchStore.getState();
      if (isMobile && wb.leftOpen) wb.toggleLeft();
    }
  };

  return (
    <div
      ref={dragHandle}
      style={style}
      className={cn(
        "flex items-center gap-1 px-1.5 rounded text-xs select-none transition-colors cursor-pointer",
        node.isSelected || isActive ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover",
        node.willReceiveDrop && "bg-accent-subtle ring-1 ring-accent",
        node.isDragging && "opacity-50",
        !openable && "opacity-50",
      )}
      onClick={handleClick}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        node.select();
        onContextMenu(e, data);
      }}
      onDragStart={(e) => {
        // 拖拽注入对话仅工作区文件可用（与库内移动拖拽共用同一次拖动，数据类型互不干扰）
        if (isDir || root !== "workspace") return;
        e.dataTransfer.setData(WORKSPACE_FILE_MIME, JSON.stringify({ path: data.path, name: data.name }));
        e.dataTransfer.effectAllowed = "copyMove";
      }}
      title={data.path}
    >
      {isDir ? (
        <>
          {loading ? (
            <Loader2 size={11} className="animate-spin shrink-0 text-muted" />
          ) : node.isOpen ? (
            <ChevronDown size={11} className="shrink-0 text-muted" />
          ) : (
            <ChevronRight size={11} className="shrink-0 text-muted" />
          )}
          {node.isOpen ? (
            <FolderOpen size={13} className="shrink-0 text-accent" />
          ) : (
            <FolderClosed size={13} className="shrink-0 text-muted" />
          )}
        </>
      ) : (
        <>
          <span className="w-[11px] shrink-0" />
          <Icon size={13} className={cn("shrink-0", iconClass)} />
        </>
      )}
      {node.isEditing ? (
        <RenameInput node={node} />
      ) : (
        <span className="truncate flex-1 min-w-0">{data.name}</span>
      )}
      {isOpened && <span className="w-1 h-1 rounded-full bg-accent shrink-0" aria-label="opened" />}
      {isMobile && !node.isEditing && (
        <button
          className="p-1 -mr-1 rounded text-muted hover:text-foreground shrink-0"
          aria-label="more"
          onClick={(e) => {
            e.stopPropagation();
            node.select();
            onContextMenu(e, data);
          }}
        >
          <MoreHorizontal size={13} />
        </button>
      )}
    </div>
  );
}
