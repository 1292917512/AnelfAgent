import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";
import type { ChatMessage } from "@/lib/types";
import { MediaPreview, type MediaKind } from "./MediaPreview";

/** 媒体消息气泡：图片灯箱 / 视频弹层播放 / 音频 / 文件下载卡片 */
export function MediaBubble({ msg }: { msg: ChatMessage }) {
  const { t } = useTranslation("chat");
  const mt = msg.media_type;
  const url = msg.url || "";

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
  if (mt === "image" || mt === "video" || mt === "voice" || mt === "audio") {
    return <MediaPreview kind={mt as MediaKind} url={url} alt={msg.caption} />;
  }
  return null;
}
