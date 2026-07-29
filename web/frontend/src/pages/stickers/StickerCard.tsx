import type { TOptions } from "i18next";
import { Pencil, RefreshCw, Trash2 } from "lucide-react";
import { stickersApi } from "@/lib/api";
import type { StickerItem } from "@/lib/types";

export function StickerCard({
  sticker,
  onEdit,
  onDelete,
  onReindex,
  reindexing,
  t,
}: {
  sticker: StickerItem;
  onEdit: () => void;
  onDelete: () => void;
  onReindex: () => void;
  reindexing: boolean;
  t: (k: string, opts?: TOptions) => string;
}) {
  return (
    <div className="group flex flex-col rounded-md border border-border bg-secondary overflow-hidden hover:border-border-strong transition-colors">
      <div className="relative aspect-square bg-panel flex items-center justify-center overflow-hidden">
        <img
          src={stickersApi.fileUrl(sticker.id)}
          alt={sticker.description}
          loading="lazy"
          className="max-w-full max-h-full object-contain"
        />
        {/* 操作按钮（触屏常显） */}
        <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
          <button
            onClick={onEdit}
            className="p-1.5 rounded-md bg-panel/90 border border-border text-muted hover:text-accent transition-colors"
            title={t("edit")}
          >
            <Pencil size={13} />
          </button>
          <button
            onClick={onReindex}
            disabled={reindexing}
            className="p-1.5 rounded-md bg-panel/90 border border-border text-muted hover:text-accent transition-colors disabled:opacity-50"
            title={t("reindex")}
          >
            <RefreshCw size={13} className={reindexing ? "animate-spin" : ""} />
          </button>
          <button
            onClick={onDelete}
            className="p-1.5 rounded-md bg-panel/90 border border-border text-muted hover:text-danger transition-colors"
            title={t("common:delete")}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>
      <div className="p-2 space-y-1">
        <p className="text-[11px] text-foreground leading-snug line-clamp-2 min-h-[2em]">
          {sticker.description || <span className="text-muted/50 italic">—</span>}
        </p>
        <div className="flex items-center gap-1 flex-wrap">
          {sticker.emotion && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
              {sticker.emotion}
            </span>
          )}
          {sticker.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="text-[9px] px-1.5 py-0.5 rounded-full bg-secondary text-muted border border-border"
            >
              {tag}
            </span>
          ))}
          <span className="ml-auto text-[9px] text-muted">
            {t("usedTimes", { count: sticker.use_count })}
          </span>
        </div>
      </div>
    </div>
  );
}
