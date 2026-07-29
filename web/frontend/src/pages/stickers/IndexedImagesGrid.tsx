import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import { stickersApi } from "@/lib/api";
import type { IndexedImage, IndexedImageListResult } from "@/lib/types";
import { LoadingBlock } from "@/components/ui";
import { Pagination } from "@/components/common/Pagination";

/** 图片索引 Tab：网格 + 分页 */
export function IndexedImagesGrid({
  data,
  loading,
  page,
  onPageChange,
  onRemove,
}: {
  data: IndexedImageListResult | undefined;
  loading: boolean;
  page: number;
  onPageChange: (page: number) => void;
  onRemove: (path: string) => void;
}) {
  const { t } = useTranslation(["stickers", "common"]);
  return (
    <>
      <p className="text-xs text-muted">{t("imagesHint")}</p>
      {loading ? (
        <LoadingBlock label={t("common:loading")} />
      ) : !data || data.items.length === 0 ? (
        <p className="text-sm text-muted text-center py-10">{t("noImages")}</p>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
            {data.items.map((img: IndexedImage) => (
              <div
                key={img.path}
                className="group flex flex-col rounded-md border border-border bg-secondary overflow-hidden"
              >
                <div className="relative aspect-square bg-panel flex items-center justify-center overflow-hidden">
                  <img
                    src={stickersApi.imageFileUrl(img.path)}
                    alt={img.description}
                    loading="lazy"
                    className="max-w-full max-h-full object-contain"
                  />
                  <button
                    onClick={() => onRemove(img.path)}
                    className="absolute top-1.5 right-1.5 p-1.5 rounded-md bg-panel/90 border border-border
                      text-muted hover:text-danger opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-all"
                    title={t("removeFromIndex")}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                <div className="p-2">
                  <p className="text-[11px] text-foreground leading-snug line-clamp-2 min-h-[2em]">
                    {img.description || <span className="text-muted/50 italic">{t("noDescription")}</span>}
                  </p>
                  <p className="text-[9px] text-muted truncate" title={img.path}>
                    {img.path.split("/").pop()}
                  </p>
                </div>
              </div>
            ))}
          </div>
          <Pagination
            page={page}
            total={data.total}
            pageSize={data.page_size}
            onChange={onPageChange}
          />
        </>
      )}
    </>
  );
}
