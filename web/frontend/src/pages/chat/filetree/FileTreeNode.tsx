import { workspaceFileId } from "@/lib/workspace-file";
import { createContext, useContext, useEffect, useRef, useState, type MouseEvent } from "react";
import type { NodeRendererProps, RowRendererProps } from "react-arborist";
import { ChevronDown, ChevronRight, FolderClosed, FolderOpen, Loader2, MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/lib/use-media-query";
import type { WorkspaceNode, WorkspaceRoot } from "@/lib/types";
import { workspaceMediaKind, isPreviewableBinary } from "@/lib/workspace-kind";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useFileTreeStore } from "./file-tree-store";
import { fileIcon } from "./file-tree-utils";
import { dirChangeCount, useTreeChangesStore } from "@/stores/tree-changes-store";

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

/** 重命名内联输入框：Enter/失焦提交，Esc 取消（文件名预选主名部分；目录链节点取最深段） */
function RenameInput({ node }: { node: NodeRendererProps<WorkspaceNode>["node"] }) {
  const ref = useRef<HTMLInputElement>(null);
  // 目录链（a/b/c）的重命名作用于最深目录，编辑框只给最深段名
  const editName = node.data.name.split("/").pop() ?? node.data.name;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const dot = editName.lastIndexOf(".");
    el.setSelectionRange(0, dot > 0 && node.data.type === "file" ? dot : editName.length);
  }, [node, editName]);
  return (
    <input
      ref={ref}
      defaultValue={editName}
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

/** 文件树节点行（react-arborist 虚拟化渲染）：图标 / 重命名编辑 / 选择高亮 / 拖放 / 变更徽章 */
export function FileTreeNode(props: NodeRendererProps<WorkspaceNode>) {
  const { node, style, dragHandle } = props;
  const { root, onContextMenu } = useFileTreeContext();
  const isMobile = useIsMobile();
  const openFiles = useWorkbenchStore((s) => s.openFiles);
  const activeFileId = useWorkbenchStore((s) => s.activeFileId);
  const openFile = useWorkbenchStore((s) => s.openFile);
  const loading = useFileTreeStore((s) => s.loadingDirs[`${root}:${node.data.path}`] === true);
  const changeEntries = useTreeChangesStore((s) => s.entries);
  const flashSeq = useTreeChangesStore((s) => s.flashSeqs[node.data.path] ?? 0);

  const data = node.data;
  const isDir = data.type === "dir";
  const isActive = activeFileId === workspaceFileId({ path: data.path, root });
  const isOpened = !isActive && openFiles.some((file) => file.path === data.path && file.root === root);
  // 二进制中的图片/音视频/PDF/DOCX/XLSX 可打开预览，其余二进制不可编辑
  const openable = isDir || !data.binary || workspaceMediaKind(data.name) !== null || isPreviewableBinary(data.name);
  const { Icon, className: iconClass } = fileIcon(data.name);

  // 变更徽章：AI 编辑只会落在工作区根（project 根不装饰，避免同名路径误标）；
  // 文件取自身 +a/-r，目录聚合子树变更数
  const decorate = root === "workspace";
  const selfChange = decorate && !isDir ? (changeEntries[data.path] ?? null) : null;
  const subTreeChanges = decorate && isDir ? dirChangeCount(changeEntries, data.path) : 0;

  // 变更闪现：seq 变化时播放一次高亮动画（仅工作区根）
  const [flashing, setFlashing] = useState(false);
  useEffect(() => {
    if (!flashSeq || !decorate) return;
    setFlashing(true);
    const timer = setTimeout(() => setFlashing(false), 1200);
    return () => clearTimeout(timer);
  }, [flashSeq, decorate]);

  const handleClick = () => {
    node.select();
    if (isDir) {
      node.toggle();
    } else if (openable) {
      openFile(data.path, root);
    }
  };

  return (
    <div
      ref={dragHandle}
      style={style}
      className={cn(
        "flex h-full items-center gap-1 px-1.5 rounded text-xs select-none transition-colors cursor-pointer",
        node.isSelected || isActive ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover",
        node.willReceiveDrop && "bg-accent-subtle ring-1 ring-accent",
        node.isDragging && "opacity-50",
        !openable && "opacity-50",
        flashing && "anelf-tree-node-flash",
      )}
      onClick={handleClick}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        node.select();
        onContextMenu(e, data);
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
      {/* 变更徽章：文件 +a/-r（等宽防抖动），目录聚合子树变更计数 */}
      {selfChange && !node.isEditing && (
        <span className="flex items-center gap-1 shrink-0 font-mono text-[9px] tabular-nums" aria-label="changed">
          {selfChange.additions > 0 && <span className="text-green-600">+{selfChange.additions}</span>}
          {selfChange.removals > 0 && <span className="text-red-500">-{selfChange.removals}</span>}
          {selfChange.additions === 0 && selfChange.removals === 0 && (
            <span className="w-1.5 h-1.5 rounded-full bg-accent" />
          )}
        </span>
      )}
      {subTreeChanges > 0 && !node.isEditing && (
        <span
          className="shrink-0 min-w-3.5 px-0.5 rounded-full bg-accent-subtle text-accent text-[9px] leading-3.5 text-center tabular-nums"
          aria-label="subtree-changes"
        >
          {subTreeChanges}
        </span>
      )}
      {isOpened && <span className="w-1 h-1 rounded-full bg-accent shrink-0" aria-label="opened" />}
      {isMobile && !node.isEditing && (
        <button
          className="flex h-full w-10 -mr-1 items-center justify-center rounded text-muted hover:text-foreground shrink-0"
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
