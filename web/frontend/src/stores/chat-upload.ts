/**
 * 聊天文件上传：附件分类、数量/大小校验、上传与预览 URL 管理。
 *
 * 从 chat-store 拆出；通过 ChatUploadContext 回调操作 bucket 状态，避免循环依赖。
 */
import i18n from "@/i18n";
import { chatApi } from "@/lib/api";
import type { ChatBucket, PendingFile } from "@/lib/types";
import { useWorkbenchStore } from "./workbench-store";
import { nextCid } from "./chat-shared";

export const MAX_FILES = 9;
export const MAX_FILE_SIZE = 50 * 1024 * 1024;

export function classifyFile(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"].includes(ext)) return "image";
  if (["mp3", "wav", "ogg", "flac", "m4a", "amr", "opus"].includes(ext)) return "audio";
  if (["mp4", "avi", "mkv", "mov", "webm", "flv"].includes(ext)) return "video";
  return "file";
}

export interface ChatUploadContext {
  updateBucket: (chatId: string, fn: (b: ChatBucket) => Partial<ChatBucket>) => void;
}

function notify(content: string) {
  useWorkbenchStore.getState().pushNotification({
    id: nextCid(),
    title: "",
    content,
    level: "warning",
    ts: Date.now() / 1000,
  });
}

/** 校验并过滤待添加文件（数量上限 / 单文件大小），返回可接受列表 */
export function filterAcceptedFiles(files: FileList | null, currentCount: number): File[] {
  if (!files) return [];
  const accepted: File[] = [];
  for (const file of Array.from(files)) {
    if (currentCount + accepted.length >= MAX_FILES) {
      notify(i18n.t("fileLimit", { ns: "chat", max: MAX_FILES }));
      break;
    }
    if (file.size > MAX_FILE_SIZE) {
      notify(i18n.t("fileTooLarge", { ns: "chat", name: file.name, max: 50 }));
      continue;
    }
    accepted.push(file);
  }
  return accepted;
}

/** 构造 PendingFile（图片生成 blob: 预览 URL；所有权随 pendingFiles/消息 content 管理） */
export function makePendingFile(file: File): PendingFile {
  const type = classifyFile(file.name);
  const pf: PendingFile = { file, type, uploading: true };
  if (type === "image") pf.preview = URL.createObjectURL(file);
  return pf;
}

/** 逐个上传附件并回写 bucket 状态（成功后带 path，失败仅复位 uploading） */
export async function uploadPendingFiles(
  chatId: string,
  newFiles: PendingFile[],
  ctx: ChatUploadContext,
): Promise<void> {
  for (const pf of newFiles) {
    try {
      const resp = await chatApi.upload(pf.file);
      const data = resp.data as { path: string; url: string };
      ctx.updateBucket(chatId, (b) => ({
        pendingFiles: b.pendingFiles.map((f) =>
          f.file === pf.file ? { ...f, uploading: false, path: data.path } : f,
        ),
      }));
    } catch {
      ctx.updateBucket(chatId, (b) => ({
        pendingFiles: b.pendingFiles.map((f) =>
          f.file === pf.file ? { ...f, uploading: false } : f,
        ),
      }));
    }
  }
}
