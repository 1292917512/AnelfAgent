import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FileWarning, Loader2, X, ZoomIn, ZoomOut } from "lucide-react";
import { cn } from "@/lib/utils";
import { DialogSurface } from "@/components/ui/DialogSurface";

interface LightboxProps {
  open?: boolean;
  src: string;
  alt?: string;
  onClose: () => void;
}

/** 轻量图片灯箱：全屏遮罩 + 点击缩放 + ESC/点击关闭 + 加载态/失败兜底 */
export function Lightbox({ open = true, src, alt, onClose }: LightboxProps) {
  const { t } = useTranslation("chat");
  const [zoomed, setZoomed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  return (
    <DialogSurface open={open} onClose={onClose} title={t("imagePreview")} placement="fullscreen" className="z-[120] border-0 bg-black/80">
      <div className="absolute top-3 right-3 flex items-center gap-2 z-10">
        {!failed && (
          <button
            onClick={(e) => { e.stopPropagation(); setZoomed((z) => !z); }}
            className="p-2 rounded-md bg-black/50 text-white/80 hover:text-white transition-colors"
            aria-label={t(zoomed ? "zoomOut" : "zoomIn")}
          >
            {zoomed ? <ZoomOut size={18} /> : <ZoomIn size={18} />}
          </button>
        )}
        <button
          onClick={onClose}
          className="p-2 rounded-md bg-black/50 text-white/80 hover:text-white transition-colors"
          aria-label={t("common:close")}
        >
          <X size={18} />
        </button>
      </div>
      <div className="flex flex-1 min-h-0 items-center justify-center overflow-auto" onClick={onClose}>
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
      </div>
    </DialogSurface>
  );
}

/** 灯箱状态管理 Hook：在渲染器中注入 onImageClick */
export function useLightbox(): { lightbox: ReactNode; openLightbox: (src: string, alt?: string) => void } {
  const [state, setState] = useState<{ src: string; alt?: string } | null>(null);
  const [open, setOpen] = useState(false);
  const openLightbox = useCallback((src: string, alt?: string) => { setState({ src, alt }); setOpen(true); }, []);
  return {
    lightbox: state ? <Lightbox key={state.src} open={open} src={state.src} alt={state.alt} onClose={() => setOpen(false)} /> : null,
    openLightbox,
  };
}
