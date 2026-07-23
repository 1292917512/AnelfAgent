import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { stickersApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { StickerItem } from "@/lib/types";
import { Button, ConfirmDialog, Input, LoadingBlock, Modal, Textarea, toast } from "@/components/ui";
import {
  Image as ImageIcon,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Smile,
  Trash2,
  Upload,
} from "lucide-react";

type Tab = "stickers" | "images";

function StickerCard({
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
  t: (k: string, opts?: Record<string, unknown>) => string;
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
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  const totalPages = (total: number, size: number) => Math.max(1, Math.ceil(total / size));

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
              <div className="flex items-center justify-center gap-3">
                <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                  {t("common:prev")}
                </Button>
                <span className="text-xs text-muted">
                  {page} / {totalPages(stickerData.total, stickerData.page_size)}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page >= totalPages(stickerData.total, stickerData.page_size)}
                  onClick={() => setPage(page + 1)}
                >
                  {t("common:next")}
                </Button>
              </div>
            </>
          )}
        </>
      )}

      {/* 图片索引页 */}
      {tab === "images" && (
        <>
          <p className="text-xs text-muted">{t("imagesHint")}</p>
          {imagesLoading ? (
            <LoadingBlock label={t("common:loading")} />
          ) : !imageData || imageData.items.length === 0 ? (
            <p className="text-sm text-muted text-center py-10">{t("noImages")}</p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
                {imageData.items.map((img) => (
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
                        onClick={() => setRemoveImageTarget(img.path)}
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
              <div className="flex items-center justify-center gap-3">
                <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                  {t("common:prev")}
                </Button>
                <span className="text-xs text-muted">
                  {page} / {totalPages(imageData.total, imageData.page_size)}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page >= totalPages(imageData.total, imageData.page_size)}
                  onClick={() => setPage(page + 1)}
                >
                  {t("common:next")}
                </Button>
              </div>
            </>
          )}
        </>
      )}

      {/* 上传弹窗 */}
      <Modal
        open={showUpload}
        onClose={() => setShowUpload(false)}
        width="max-w-md"
        title={
          <span className="flex items-center gap-2">
            <Upload size={18} className="text-accent" />
            {t("uploadTitle")}
          </span>
        }
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setShowUpload(false)}>
              {t("common:cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => uploadMut.mutate()}
              disabled={!uploadFile}
              loading={uploadMut.isPending}
            >
              {uploadMut.isPending ? t("common:saving") : t("common:create")}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("imageFile")}</label>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
            />
            <Button variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()}>
              <Upload size={14} />
              {uploadFile ? uploadFile.name : t("chooseFile")}
            </Button>
            {uploadFile && (
              <img
                src={URL.createObjectURL(uploadFile)}
                alt="preview"
                className="mt-2 max-h-40 rounded-md border border-border object-contain"
              />
            )}
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("description")}</label>
            <Textarea
              value={uploadForm.description}
              onChange={(e) => setUploadForm({ ...uploadForm, description: e.target.value })}
              placeholder={t("descriptionPlaceholder")}
              rows={3}
              className="resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("tags")}</label>
            <Input
              value={uploadForm.tags}
              onChange={(e) => setUploadForm({ ...uploadForm, tags: e.target.value })}
              placeholder={t("tagsPlaceholder")}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("emotion")}</label>
            <Input
              value={uploadForm.emotion}
              onChange={(e) => setUploadForm({ ...uploadForm, emotion: e.target.value })}
              placeholder={t("emotionPlaceholder")}
            />
          </div>
        </div>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        width="max-w-md"
        title={
          <span className="flex items-center gap-2">
            <Pencil size={18} className="text-accent" />
            {t("editTitle")}
          </span>
        }
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setEditTarget(null)}>
              {t("common:cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => editMut.mutate()}
              loading={editMut.isPending}
            >
              {editMut.isPending ? t("common:saving") : t("common:save")}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {editTarget && (
            <div className="flex justify-center">
              <img
                src={stickersApi.fileUrl(editTarget.id)}
                alt={editTarget.description}
                className="max-h-40 rounded-md border border-border object-contain"
              />
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("description")}</label>
            <Textarea
              value={editForm.description}
              onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
              rows={3}
              className="resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("tags")}</label>
            <Input
              value={editForm.tags}
              onChange={(e) => setEditForm({ ...editForm, tags: e.target.value })}
              placeholder={t("tagsPlaceholder")}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("emotion")}</label>
            <Input
              value={editForm.emotion}
              onChange={(e) => setEditForm({ ...editForm, emotion: e.target.value })}
              placeholder={t("emotionPlaceholder")}
            />
          </div>
        </div>
      </Modal>

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
