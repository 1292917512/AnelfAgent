import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { Pencil, Upload } from "lucide-react";
import { stickersApi } from "@/lib/api";
import type { StickerItem } from "@/lib/types";
import { Button, Input, Modal, Textarea } from "@/components/ui";

export interface StickerFormState {
  description: string;
  tags: string;
  emotion: string;
}

/** 上传表情包弹窗 */
export function UploadStickerModal({
  open,
  onClose,
  uploadFile,
  setUploadFile,
  uploadForm,
  setUploadForm,
  previewUrl,
  pending,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  uploadFile: File | null;
  setUploadFile: (f: File | null) => void;
  uploadForm: StickerFormState;
  setUploadForm: (f: StickerFormState) => void;
  previewUrl: string | null;
  pending: boolean;
  onSubmit: () => void;
}) {
  const { t } = useTranslation(["stickers", "common"]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  return (
    <Modal
      open={open}
      onClose={onClose}
      width="max-w-md"
      title={
        <span className="flex items-center gap-2">
          <Upload size={18} className="text-accent" />
          {t("uploadTitle")}
        </span>
      }
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={onSubmit}
            disabled={!uploadFile}
            loading={pending}
          >
            {pending ? t("common:saving") : t("common:create")}
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
          {previewUrl && (
            <img
              src={previewUrl}
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
  );
}

/** 编辑表情包弹窗 */
export function EditStickerModal({
  target,
  onClose,
  editForm,
  setEditForm,
  pending,
  onSubmit,
}: {
  target: StickerItem | null;
  onClose: () => void;
  editForm: StickerFormState;
  setEditForm: (f: StickerFormState) => void;
  pending: boolean;
  onSubmit: () => void;
}) {
  const { t } = useTranslation(["stickers", "common"]);
  return (
    <Modal
      open={!!target}
      onClose={onClose}
      width="max-w-md"
      title={
        <span className="flex items-center gap-2">
          <Pencil size={18} className="text-accent" />
          {t("editTitle")}
        </span>
      }
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={onSubmit}
            loading={pending}
          >
            {pending ? t("common:saving") : t("common:save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {target && (
          <div className="flex justify-center">
            <img
              src={stickersApi.fileUrl(target.id)}
              alt={target.description}
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
  );
}
