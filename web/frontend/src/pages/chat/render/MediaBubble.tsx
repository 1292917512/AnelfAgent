import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Play } from "lucide-react";
import type { ChatMessage } from "@/stores/chat-store";
import { Lightbox } from "./Lightbox";

/** 媒体消息气泡：图片灯箱 / 视频弹层播放 / 音频 / 文件下载卡片 */
export function MediaBubble({ msg }: { msg: ChatMessage }) {
  const { t } = useTranslation("chat");
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [videoOpen, setVideoOpen] = useState(false);
  const mt = msg.media_type;
  const url = msg.url || "";

  if (mt === "image" && url) {
    return (
      <>
        <img
          src={url}
          alt={msg.caption || ""}
          onClick={() => setLightboxSrc(url)}
          className="max-w-full sm:max-w-xs rounded-md cursor-zoom-in hover:opacity-90 transition-opacity"
        />
        {lightboxSrc && <Lightbox src={lightboxSrc} alt={msg.caption} onClose={() => setLightboxSrc(null)} />}
      </>
    );
  }
  if (mt === "voice" || mt === "audio") {
    return <audio controls src={url} className="max-w-[280px]" />;
  }
  if (mt === "video" && url) {
    if (!videoOpen) {
      return (
        <button
          onClick={() => setVideoOpen(true)}
          className="relative group max-w-full sm:max-w-xs rounded-md overflow-hidden border border-border bg-elevated"
        >
          <video src={url} preload="metadata" muted className="max-w-full rounded-md pointer-events-none" />
          <span className="absolute inset-0 flex items-center justify-center bg-black/30 group-hover:bg-black/45 transition-colors">
            <span className="w-11 h-11 rounded-full bg-black/60 flex items-center justify-center">
              <Play size={18} className="text-white ml-0.5" />
            </span>
          </span>
        </button>
      );
    }
    return <video controls autoPlay src={url} className="max-w-full sm:max-w-sm rounded-md" />;
  }
  if (mt === "file" && url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="flex items-center gap-2 px-3 py-2 rounded-md bg-elevated border border-border text-xs text-accent hover:underline"
      >
        <FileText size={14} /> {msg.caption || t("downloadFile")}
      </a>
    );
  }
  return null;
}
