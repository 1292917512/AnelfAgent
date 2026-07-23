import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, MonitorX } from "lucide-react";
import type Mpegts from "mpegts.js";
import { workspaceApi, workspaceVideoSupport } from "@/lib/api";

interface VideoPreviewProps {
  path: string;
  name: string;
}

/** 视频预览：mp4/webm/mov 原生播放，flv 经 mpegts.js（MSE）播放，mkv/avi 提示下载 */
export function VideoPreview({ path, name }: VideoPreviewProps) {
  const { t } = useTranslation("workbench");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [flvFailed, setFlvFailed] = useState(false);
  const support = workspaceVideoSupport(name);
  const url = workspaceApi.rawUrl(path);

  // flv 经 mpegts.js 转 MSE 播放（库按需动态加载），卸载时销毁播放器释放媒体资源
  useEffect(() => {
    if (support !== "flv") return;
    const el = videoRef.current;
    if (!el) return;
    let player: Mpegts.Player | null = null;
    let cancelled = false;
    setFlvFailed(false);
    (async () => {
      try {
        const mpegts = (await import("mpegts.js")).default;
        if (cancelled) return;
        if (!mpegts.isSupported()) {
          setFlvFailed(true);
          return;
        }
        player = mpegts.createPlayer({ type: "flv", isLive: false, url });
        player.attachMediaElement(el);
        player.load();
      } catch {
        if (!cancelled) setFlvFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      player?.destroy();
    };
  }, [support, url]);

  if (support === "unsupported" || flvFailed) {
    return (
      <div className="flex flex-col items-center gap-3 py-12 text-sm text-muted">
        <MonitorX size={28} className="text-muted" />
        <p>{t("editor.videoUnsupported")}</p>
        <a
          href={url}
          download={name}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs text-accent bg-accent-subtle hover:opacity-80 transition-opacity"
        >
          <Download size={13} /> {t("editor.download")}
        </a>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center py-4">
      <video
        ref={videoRef}
        controls
        src={support === "native" ? url : undefined}
        className="max-w-full max-h-[70vh] rounded-md border border-border"
      />
    </div>
  );
}
