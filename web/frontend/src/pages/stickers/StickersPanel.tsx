import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { stickersApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { StickerItem } from "@/lib/types";
import { Button, ConfirmDialog, Input, LoadingBlock, toast } from "@/components/ui";
import {
  Image as ImageIcon,
  Plus,
  Search,
  Smile,
} from "lucide-react";
import { Pagination } from "@/components/common/Pagination";
import { StickerCard } from "./StickerCard";
import { IndexedImagesGrid } from "./IndexedImagesGrid";
import { EditStickerModal, UploadStickerModal } from "./StickerModals";

type Tab = "stickers" | "images";

/** 表情包管理面板 — 可嵌入「数据管理」页 Tab，也可由 /stickers 独立页复用 */
export function StickersPanel() {
  const { t } = useTranslation(["stickers", "common"]);
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("stickers");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [showUpload, setShowUpload] = useState(false);
  const [editTarget, setEditTarget] = useState<StickerItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<StickerItem | null>(null);
  const [removeImageTarget, setRemoveImageTarget] = useState<string | null>(null);
  const [uploadForm, setUploadForm] = useState({ description: "", tags: "", emotion: "" });
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [editForm, setEditForm] = useState({ description: "", tags: "", emotion: "" });

  const { data: stats } = useQuery({
    queryKey: ["sticker-stats"],
    queryFn: () => stickersApi.stats().then((r) => r.data),
  });

  const { data: stickerData, isLoading: stickersLoading } = useQuery({
    queryKey: ["stickers", search, page],
    queryFn: () =>
      stickersApi.list({ query: search, page, page_size: 24 }).then((r) => r.data),
    enabled: tab === "stickers",
  });

  const { data: imageData, isLoading: imagesLoading } = useQuery({
    queryKey: ["indexed-images", page],
    queryFn: () => stickersApi.listImages({ page, page_size: 24 }).then((r) => r.data),
    enabled: tab === "images",
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["stickers"] });
    queryClient.invalidateQueries({ queryKey: ["indexed-images"] });
    queryClient.invalidateQueries({ queryKey: ["sticker-stats"] });
  };

  const uploadMut = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      if (uploadFile) fd.append("file", uploadFile);
      fd.append("description", uploadForm.description);
      fd.append("tags", uploadForm.tags);
      fd.append("emotion", uploadForm.emotion);
      return stickersApi.upload(fd);
    },
    onSuccess: () => {
      toast.success(t("uploadSuccess"));
      setShowUpload(false);
      setUploadFile(null);
      setUploadForm({ description: "", tags: "", emotion: "" });
      invalidate();
    },
    onError: () => toast.error(t("uploadFailed")),
  });

  const editMut = useMutation({
    mutationFn: () => {
      if (!editTarget) return Promise.reject();
      return stickersApi.update(editTarget.id, {
        description: editForm.description,
        tags: editForm.tags.split(/[,，、\s]+/).filter(Boolean),
        emotion: editForm.emotion,
      });
    },
    onSuccess: () => {
      setEditTarget(null);
      invalidate();
    },
    onError: () => toast.error(t("saveFailed")),
  });

  const reindexMut = useMutation({
    mutationFn: (id: string) => stickersApi.reindex(id),
    onSuccess: () => {
      toast.success(t("reindexSuccess"));
      invalidate();
    },
    onError: () => toast.error(t("reindexFailed")),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => stickersApi.remove(id),
    onSuccess: () => {
      setDeleteTarget(null);
      invalidate();
    },
  });

  const removeImageMut = useMutation({
    mutationFn: (path: string) => stickersApi.removeImage(path),
    onSuccess: () => {
      setRemoveImageTarget(null);
      invalidate();
    },
  });

  const openEdit = (s: StickerItem) => {
    setEditForm({ description: s.description, tags: s.tags.join(", "), emotion: s.emotion });
    setEditTarget(s);
  };

  // 上传预览图：blob URL 生命周期跟随 uploadFile，卸载/更换时回收
  const [uploadPreviewUrl, setUploadPreviewUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!uploadFile) {
      setUploadPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(uploadFile);
    setUploadPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [uploadFile]);

  return (
    <>
      {/* 统计 + 上传 */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
              {t("statsStickers", { count: stats?.stickers ?? 0 })}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
              {t("statsUses", { count: stats?.total_uses ?? 0 })}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
              {t("statsImages", { count: stats?.indexed_images ?? 0 })}
            </span>
            {stats && (
              <span
                className={cn(
                  "text-xs px-2 py-0.5 rounded-full border",
                  stats.vec_available
                    ? "bg-accent/10 text-accent border-accent/20"
                    : "bg-secondary text-muted border-border",
                )}
              >
                {stats.vec_available ? t("vecOn") : t("vecOff")}
              </span>
            )}
          </div>
          <p className="text-xs text-muted max-w-xl">{t("subtitle")}</p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setShowUpload(true)} className="shrink-0">
          <Plus size={14} />
          {t("uploadSticker")}
        </Button>
      </div>

      {/* Tab 切换 */}
      <div className="flex gap-1 border-b border-border">
        {(["stickers", "images"] as Tab[]).map((key) => (
          <button
            key={key}
            onClick={() => { setTab(key); setPage(1); }}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              tab === key
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-foreground",
            )}
          >
            {key === "stickers" ? <Smile size={15} /> : <ImageIcon size={15} />}
            {t(key === "stickers" ? "tabStickers" : "tabImages")}
          </button>
        ))}
      </div>

      {/* 表情包页 */}
      {tab === "stickers" && (
        <>
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <Input
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              placeholder={t("searchPlaceholder")}
              className="pl-9"
            />
          </div>
          {stickersLoading ? (
            <LoadingBlock label={t("common:loading")} />
          ) : !stickerData || stickerData.items.length === 0 ? (
            <p className="text-sm text-muted text-center py-10">
              {search ? t("noMatch") : t("noStickers")}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
                {stickerData.items.map((s) => (
                  <StickerCard
                    key={s.id}
                    sticker={s}
                    onEdit={() => openEdit(s)}
                    onDelete={() => setDeleteTarget(s)}
                    onReindex={() => reindexMut.mutate(s.id)}
                    reindexing={reindexMut.isPending && reindexMut.variables === s.id}
                    t={t}
                  />
                ))}
              </div>
              <Pagination
                page={page}
                total={stickerData.total}
                pageSize={stickerData.page_size}
                onChange={setPage}
              />
            </>
          )}
        </>
      )}

      {tab === "images" && (
        <IndexedImagesGrid
          data={imageData}
          loading={imagesLoading}
          page={page}
          onPageChange={setPage}
          onRemove={setRemoveImageTarget}
        />
      )}

      <UploadStickerModal
        open={showUpload}
        onClose={() => setShowUpload(false)}
        uploadFile={uploadFile}
        setUploadFile={setUploadFile}
        uploadForm={uploadForm}
        setUploadForm={setUploadForm}
        previewUrl={uploadPreviewUrl}
        pending={uploadMut.isPending}
        onSubmit={() => uploadMut.mutate()}
      />

      <EditStickerModal
        target={editTarget}
        onClose={() => setEditTarget(null)}
        editForm={editForm}
        setEditForm={setEditForm}
        pending={editMut.isPending}
        onSubmit={() => editMut.mutate()}
      />

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
        title={t("common:delete")}
        message={t("deleteConfirm", { id: deleteTarget?.id ?? "" })}
        confirmText={deleteMut.isPending ? t("common:saving") : t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={deleteMut.isPending}
      />

      {/* 移出索引确认 */}
      <ConfirmDialog
        open={!!removeImageTarget}
        onClose={() => setRemoveImageTarget(null)}
        onConfirm={() => removeImageTarget && removeImageMut.mutate(removeImageTarget)}
        title={t("removeFromIndex")}
        message={t("removeImageConfirm")}
        confirmText={removeImageMut.isPending ? t("common:saving") : t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={removeImageMut.isPending}
      />
    </>
  );
}
