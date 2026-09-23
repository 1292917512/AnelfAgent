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
import {
  consumeWorkspaceDragPayload,
  hasWorkspaceFileDrag,
} from "./workspace-drag";
import { cn } from "@/lib/utils";



export function ChatDropZone({ children, className }: { children: ReactNode; className?: string }) {
  const { t } = useTranslation("chat");
  const [active, setActive] = useState(false);
  // 进入/离开的嵌套计数（dragenter/dragleave 在子元素间交替触发）
  const depthRef = useRef(0);
  const attachWorkspaceFile = useChatStore((s) => s.attachWorkspaceFile);
  const attachWorkspaceDir = useChatStore((s) => s.attachWorkspaceDir);
  const addFiles = useChatStore((s) => s.addFiles);

  const onDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault();
    if (!hasWorkspaceFileDrag()) return; // 外部文件拖入：可放但不显示「附加文件」遮罩
    depthRef.current += 1;
    setActive(true);
  }, []);

  const onDragLeave = useCallback(() => {
    depthRef.current = Math.max(0, depthRef.current - 1);
    if (depthRef.current === 0) setActive(false);
  }, []);

  // dragover 无条件 preventDefault——drop 只在被 preventDefault 的元素上生效，
  // 而 hasWorkspaceFileDrag 的模块变量判定与 React 渲染闭包存在时序脱节
  // （dragstart 原生监听器设 payload 的时机不一定赶得上本次 dragover 的判定）。
  // 放开时由 consumeWorkspaceDragPayload 分流：有 payload 走工作区引用，
  // 没有则交给子级（输入框 addFiles 等）处理，不挡路。
  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  // 对话区唯一的 drop 收口：工作区 payload → 引用附件；外部文件 → 上传附件
  const onDrop = useCallback((e: DragEvent) => {
    depthRef.current = 0;
    setActive(false);
    e.preventDefault();
    const payload = consumeWorkspaceDragPayload();
    if (payload) {
      if (payload.is_dir) {
        attachWorkspaceDir(payload.path, payload.name, payload.root);
      } else {
        attachWorkspaceFile(payload.path, payload.name, payload.root);
      }
      return;
    }
    if (e.dataTransfer.files.length > 0) void addFiles(e.dataTransfer.files);
  }, [attachWorkspaceFile, attachWorkspaceDir, addFiles]);

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
