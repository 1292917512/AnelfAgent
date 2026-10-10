/**
 * ChatDropZone — 工作区/项目文件拖放区（react-dnd drop target）。
 *
 * 把「拖到对话框注入文件」注册为与文件树同 dragType 的 useDrop target——
 * react-dnd 的 HTML5 backend 在「没有注册 target 命中」时给禁止光标并
 * cancel 浏览器默认 drop，所以 DOM drop 监听收不到；注册成 target 后，
 * react-dnd 在 drop 时把树节点 item 直接交给本组件。
 * 外部文件（FileList）拖入不在 react-dnd 语义内，仍走 DOM drop → addFiles。
 */

import { useCallback, useRef, useState, type DragEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FileUp } from "lucide-react";
import { DndProvider, useDrop } from "react-dnd";
import { useChatStore } from "@/stores/chat-store";
import {
  TREE_NODE_DRAG_TYPE,
  activeTreeRoot,
  sharedDndManager,
  type TreeNodeDragItem,
} from "./tree-dnd";
import { cn } from "@/lib/utils";

/** 与文件树共享 manager 的 DndProvider（useDrop 必须挂在 provider 下） */
export function ChatDropZone({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <DndProvider manager={sharedDndManager}>
      <ChatDropZoneInner className={className}>{children}</ChatDropZoneInner>
    </DndProvider>
  );
}

function ChatDropZoneInner({ children, className }: { children: ReactNode; className?: string }) {
  const { t } = useTranslation("chat");
  const attachWorkspaceFile = useChatStore((s) => s.attachWorkspaceFile);
  const attachWorkspaceDir = useChatStore((s) => s.attachWorkspaceDir);
  const addFiles = useChatStore((s) => s.addFiles);
  const dragDepth = useRef(0);
  const [filesOver, setFilesOver] = useState(false);

  // react-dnd target：树节点（含目录）落进对话区
  const [{ isOver }, dropRef] = useDrop(() => ({
    accept: TREE_NODE_DRAG_TYPE,
    drop: (item: TreeNodeDragItem) => {
      const root = activeTreeRoot();
      if (item.data.type === "dir") {
        attachWorkspaceDir(item.data.path, item.data.name, root);
      } else {
        attachWorkspaceFile(item.data.path, item.data.name, root);
      }
    },
    collect: (monitor) => ({ isOver: monitor.isOver({ shallow: true }) }),
  }), [attachWorkspaceFile, attachWorkspaceDir]);

  // 外部文件（FileList）：DOM drop（react-dnd 不管非树 item 的浏览器默认行为）
  const onDrop = useCallback((e: DragEvent) => {
    e.preventDefault();
    dragDepth.current = 0;
    setFilesOver(false);
    if (e.dataTransfer.files.length > 0) void addFiles(e.dataTransfer.files);
  }, [addFiles]);
  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  // react-dnd connector 与本地 ref 合并（connector 接收 DOM 节点）
  const setContainerRef = useCallback((el: HTMLDivElement | null) => {
    dropRef(el);
  }, [dropRef]);

  return (
    <div
      ref={setContainerRef}
      className={cn("relative", className)}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragEnter={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        dragDepth.current += 1;
        setFilesOver(true);
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (!dragDepth.current) setFilesOver(false);
      }}
    >
      {children}
      {(isOver || filesOver) && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-lg border-2 border-dashed border-accent/60 bg-accent/10"
        >
          <div className="flex items-center gap-2 rounded-full bg-popover/90 px-3 py-1.5 text-xs text-accent shadow-sm backdrop-blur-sm">
            <FileUp size={13} />
            {t("dropToAttach")}
          </div>
        </div>
      )}
    </div>
  );
}
