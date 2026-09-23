/**
 * ChatDropZone — 工作区/项目文件拖放区。
 *
 * 把「拖到对话框注入文件」从输入框局部行为扩展为整片对话中栏：
 * 拖拽进入本区域即显示高亮遮罩，放开统一走 attachWorkspaceFile。
 * 数据源兼容两种：文件树 payload（WORKSPACE_FILE_MIME，含 root）与
 * 编辑器/预览拖出的同构 payload；外部文件（FileList）不在本区消费。
 */

import { useCallback, useRef, useState, type DragEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FileUp } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { WORKSPACE_FILE_MIME } from "@/pages/chat/ChatInput";
import { cn } from "@/lib/utils";

/** 判定本次拖拽是否携带工作区文件 payload（types 需在拖拽早期即可读） */
export function hasWorkspaceFileDrag(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  return Array.from(dt.types).includes(WORKSPACE_FILE_MIME);
}

export function ChatDropZone({ children, className }: { children: ReactNode; className?: string }) {
  const { t } = useTranslation("chat");
  const [active, setActive] = useState(false);
  // 进入/离开的嵌套计数（dragenter/dragleave 在子元素间交替触发）
  const depthRef = useRef(0);
  const attachWorkspaceFile = useChatStore((s) => s.attachWorkspaceFile);

  const onDragEnter = useCallback((e: DragEvent) => {
    if (!hasWorkspaceFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    depthRef.current += 1;
    setActive(true);
  }, []);

  const onDragLeave = useCallback(() => {
    depthRef.current = Math.max(0, depthRef.current - 1);
    if (depthRef.current === 0) setActive(false);
  }, []);

  const onDragOver = useCallback((e: DragEvent) => {
    if (hasWorkspaceFileDrag(e.dataTransfer)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  }, []);

  const onDrop = useCallback((e: DragEvent) => {
    depthRef.current = 0;
    setActive(false);
    const wsData = e.dataTransfer.getData(WORKSPACE_FILE_MIME);
    if (!wsData) return;
    e.preventDefault();
    e.stopPropagation();
    try {
      const { path, name, root } = JSON.parse(wsData) as {
        path: string;
        name: string;
        root?: "workspace" | "project";
      };
      attachWorkspaceFile(path, name, root ?? "workspace");
    } catch { /* 数据异常忽略 */ }
  }, [attachWorkspaceFile]);

  return (
    <div
      className={cn("relative", className)}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {children}
      {active && (
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
