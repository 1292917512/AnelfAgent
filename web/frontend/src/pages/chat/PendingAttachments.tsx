import { FileText, Folder, Image as ImageIcon, Loader2, Music, Video, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { PendingFile } from "@/lib/types";

const icons: Record<string, typeof FileText> = { dir: Folder, image: ImageIcon, audio: Music, video: Video };

/** 待发送文件与目录引用，保持名称、来源和上传状态可见。 */
export function PendingAttachments({ files, onRemove }: { files: PendingFile[]; onRemove: (index: number) => void }) {
  const { t } = useTranslation("chat");
  if (!files.length) return null;
  return (
    <ul className="composer-attachments" aria-label={t("pendingAttachments")}>
      {files.map((file, index) => {
        const Icon = icons[file.type] ?? FileText;
        const source = file.root ? t(file.root === "project" ? "rootProject" : "rootWorkspace") : t("uploadedFile");
        return (
          <li key={`${file.path ?? file.file.name}-${index}`} className="composer-attachment" title={file.path ?? file.file.name}>
            <span className="attachment-icon" aria-hidden>
              {file.uploading ? <Loader2 size={18} className="animate-spin" /> : file.preview
                ? <img src={file.preview} alt="" /> : <Icon size={18} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-foreground">{file.file.name}</span>
              <span className="block truncate text-[11px] text-muted">
                {file.uploading ? t("uploadingFile") : !file.path ? t("uploadFailed") : `${source}${file.type === "dir" ? ` · ${t("rootDir")}` : ""}`}
              </span>
            </span>
            <button type="button" onClick={() => onRemove(index)} className="attachment-remove" aria-label={`${t("removeAttachment")}: ${file.file.name}`}>
              <X size={14} />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
