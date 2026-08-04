import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FileWarning, Play } from "lucide-react";
import { useInViewport } from "@/lib/useInViewport";
import { Lightbox } from "./Lightbox";

export type MediaKind = "image" | "video" | "audio" | "voice";

interface MediaPreviewProps {
  kind: MediaKind;
  url: string;
  alt?: string;
}

/** 加载失败兜底占位（裂图/失效 token 不再裸裂） */
function FailedPlaceholder({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2.5 rounded-md bg-elevated border border-border text-xs text-muted max-w-[280px]">
      <FileWarning size={15} className="text-warn shrink-0" />
      <span>{label}</span>
    </div>
  );
}

/**
 * 聊天媒体统一渲染（MediaBubble / ShareCard 共用）：
 * - 视口内才挂载媒体元素（IntersectionObserver，历史消息不并发请求）
 * - 图片：lazy + 点击灯箱 + onError 兜底
 * - 视频：封面占位（metadata 首帧）→ 点击切换播放 + onError 兜底
 * - 音频：controls + onError 兜底
 */
export function MediaPreview({ kind, url, alt }: MediaPreviewProps) {
  const { t } = useTranslation("chat");
  const { ref, inView } = useInViewport();
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [videoOpen, setVideoOpen] = useState(false);
  const [failed, setFailed] = useState(false);

  if (!url) return null;

  if (failed) {
    return <FailedPlaceholder label={t("mediaLoadFailed")} />;
  }

  if (kind === "image") {
    return (
      <div ref={ref} className="min-h-[48px]">
        {inView && (
          <>
            <img
              src={url}
              alt={alt || ""}
              loading="lazy"
              onClick={() => setLightboxSrc(url)}
              onError={() => setFailed(true)}
              className="max-w-full sm:max-w-xs rounded-md cursor-zoom-in hover:opacity-90 transition-opacity"
            />
            {lightboxSrc && (
              <Lightbox src={lightboxSrc} alt={alt} onClose={() => setLightboxSrc(null)} />
            )}
          </>
        )}
      </div>
    );
  }

  if (kind === "video") {
    if (videoOpen) {
      return (
        <video
          controls
          autoPlay
          src={url}
          onError={() => setFailed(true)}
          className="max-w-full sm:max-w-sm rounded-md"
        />
      );
    }
    return (
      <div ref={ref} className="min-h-[48px]">
        {inView && (
          <button
            onClick={() => setVideoOpen(true)}
            className="relative group max-w-full sm:max-w-xs rounded-md overflow-hidden border border-border bg-elevated"
          >
            <video
              src={url}
              preload="metadata"
              muted
              onError={() => setFailed(true)}
              className="max-w-full rounded-md pointer-events-none"
            />
            <span className="absolute inset-0 flex items-center justify-center bg-black/30 group-hover:bg-black/45 transition-colors">
              <span className="w-11 h-11 rounded-full bg-black/60 flex items-center justify-center">
                <Play size={18} className="text-white ml-0.5" />
              </span>
            </span>
          </button>
        )}
      </div>
    );
  }

  // audio / voice
  return (
    <div ref={ref}>
      {inView ? (
        <audio controls src={url} onError={() => setFailed(true)} className="max-w-[280px]" />
      ) : (
        <div className="min-h-[32px]" />
      )}
    </div>
  );
}
