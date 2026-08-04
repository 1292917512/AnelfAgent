import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import {
  Copy,
  Download,
  ExternalLink,
  FileText,
  Globe,
  Share2,
} from "lucide-react";
import type { ChatShareInfo } from "@/lib/types";
import { toast } from "@/stores/toast-store";
import { MediaPreview } from "./MediaPreview";

function formatSize(bytes?: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 媒体内嵌直链（inline + Range，供图片/视频/音频在卡片内渲染） */
function rawUrl(token: string): string {
  return `/api/entity/share/raw/${token}`;
}

/** 聊天分享卡片：按分享类型渲染媒体预览 / 网址卡片 / 文件下载卡 */
export function ShareCard({ share }: { share: ChatShareInfo }) {
  const { t } = useTranslation("share");

  const kind = share.media_kind;
  const isLink = share.share_type === "link";
  const size = formatSize(share.file_size);

  const copyUrl = () => {
    const target = share.url || `${window.location.origin}/api/entity/share/v/${share.token}`;
    navigator.clipboard.writeText(target).then(
      () => toast.success(t("messages.copySuccess")),
      () => toast.error(t("messages.copyFailed")),
    );
  };

  const openPreview = () => {
    const target = share.url || `/api/entity/share/v/${share.token}`;
    window.open(target, "_blank", "noopener");
  };

  // 主体预览区
  let body: ReactNode;
  if (isLink) {
    body = (
      <div className="flex items-center gap-3">
        <span className="w-10 h-10 rounded-md bg-accent-subtle flex items-center justify-center flex-shrink-0">
          <Globe size={20} className="text-accent" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-heading truncate">{share.file_name}</div>
          {share.target_url && (
            <div className="text-xs text-muted truncate">{share.target_url}</div>
          )}
        </div>
      </div>
    );
  } else if (kind === "image" || kind === "video" || kind === "audio") {
    body = <MediaPreview kind={kind} url={rawUrl(share.token)} alt={share.file_name} />;
  } else {
    // file 类型 / pdf / html：文件下载卡
    body = (
      <div className="flex items-center gap-3">
        <span className="w-10 h-10 rounded-md bg-elevated border border-border flex items-center justify-center flex-shrink-0">
          <FileText size={20} className="text-muted" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-heading truncate">{share.file_name}</div>
          {size && <div className="text-xs text-muted">{size}</div>}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-full sm:max-w-sm rounded-lg border border-border bg-secondary overflow-hidden">
      {/* 头部：分享标识 + 类型 */}
      <div className="flex items-center gap-1.5 px-3 pt-2.5 text-[11px] text-muted">
        <Share2 size={12} className="text-accent" />
        <span>{isLink ? t("types.link.name") : kind === "image" || kind === "video" || kind === "audio" ? t("types.media.name") : t("types.file.name")}</span>
        {size && !isLink && kind !== "image" && kind !== "video" && kind !== "audio" && (
          <span className="ml-auto">{size}</span>
        )}
      </div>

      {/* 主体预览 */}
      <div className="px-3 py-2">{body}</div>

      {/* 描述 */}
      {share.description && (
        <div className="px-3 pb-2 text-xs text-muted leading-relaxed">{share.description}</div>
      )}

      {/* 底部操作区 */}
      <div className="flex items-center gap-1 px-2 pb-2 pt-1 border-t border-border/50">
        <button
          onClick={openPreview}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded-md text-accent hover:bg-accent-subtle transition-colors"
        >
          <ExternalLink size={13} /> {t("actions.openPreview")}
        </button>
        {share.download_url && (
          <a
            href={share.download_url}
            download
            className="flex items-center gap-1 px-2 py-1 text-xs rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
          >
            <Download size={13} /> {t("actions.download")}
          </a>
        )}
        <button
          onClick={copyUrl}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors ml-auto"
        >
          <Copy size={13} /> {t("actions.copy")}
        </button>
      </div>
    </div>
  );
}
