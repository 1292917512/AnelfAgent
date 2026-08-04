import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { FileWarning, Loader2, X, ZoomIn, ZoomOut } from "lucide-react";
import { cn } from "@/lib/utils";

interface LightboxProps {
  src: string;
  alt?: string;
  onClose: () => void;
}

/** 轻量图片灯箱：全屏遮罩 + 点击缩放 + ESC/点击关闭 + 加载态/失败兜底 */
export function Lightbox({ src, alt, onClose }: LightboxProps) {
  const { t } = useTranslation("chat");
  const [zoomed, setZoomed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // 锁定背景滚动，关闭时恢复原值（而非硬编码清空，避免覆盖外部设置）
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[120] bg-black/80 flex items-center justify-center animate-fade-in"
      onClick={onClose}
    >
      <div className="absolute top-3 right-3 flex items-center gap-2 z-10">
        {!failed && (
          <button
            onClick={(e) => { e.stopPropagation(); setZoomed((z) => !z); }}
            className="p-2 rounded-md bg-black/50 text-white/80 hover:text-white transition-colors"
            aria-label="zoom"
          >
            {zoomed ? <ZoomOut size={18} /> : <ZoomIn size={18} />}
          </button>
        )}
        <button
          onClick={onClose}
          className="p-2 rounded-md bg-black/50 text-white/80 hover:text-white transition-colors"
          aria-label="close"
        >
          <X size={18} />
        </button>
      </div>
      {!loaded && !failed && (
        <Loader2 size={28} className="text-white/70 animate-spin absolute" />
      )}
      {failed ? (
        <div
          className="flex items-center gap-2 px-4 py-3 rounded-md bg-black/60 text-white/80 text-sm"
          onClick={(e) => e.stopPropagation()}
        >
          <FileWarning size={16} /> {t("mediaLoadFailed")}
        </div>
      ) : (
        <img
          src={src}
          alt={alt || ""}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          onClick={(e) => { e.stopPropagation(); setZoomed((z) => !z); }}
          className={cn(
            "rounded-md transition-all duration-200 select-none",
            !loaded && "opacity-0",
            zoomed ? "max-w-none max-h-none w-auto cursor-zoom-out" : "max-w-[92vw] max-h-[88vh] cursor-zoom-in",
          )}
        />
      )}
    </div>,
    document.body,
  );
}

/** 灯箱状态管理 Hook：在渲染器中注入 onImageClick */
export function useLightbox(): { lightbox: ReactNode; openLightbox: (src: string, alt?: string) => void } {
  const [state, setState] = useState<{ src: string; alt?: string } | null>(null);
  return {
    lightbox: state ? <Lightbox src={state.src} alt={state.alt} onClose={() => setState(null)} /> : null,
    openLightbox: (src, alt) => setState({ src, alt }),
  };
}
