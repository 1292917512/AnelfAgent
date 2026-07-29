import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { Button, Input, Modal, Textarea } from "@/components/ui";

export interface TagFormState {
  name: string;
  description: string;
}

/** 创建自定义标签弹窗 */
export function CreateTagModal({
  open,
  onClose,
  form,
  setForm,
  pending,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  form: TagFormState;
  setForm: (f: TagFormState) => void;
  pending: boolean;
  onSubmit: () => void;
}) {
  const { t } = useTranslation("tags");
  return (
    <Modal
      open={open}
      onClose={onClose}
      width="max-w-md"
      title={
        <span className="flex items-center gap-2">
          <Plus size={18} className="text-accent" />
          {t("createTagTitle")}
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
            disabled={!form.name.trim()}
            loading={pending}
          >
            {pending ? t("common:saving") : t("common:create")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="block text-xs font-medium text-muted mb-1">{t("tagName")}</label>
          <Input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder={t("tagNamePlaceholder")}
            className="font-mono"
          />
          <p className="text-[10px] text-muted mt-1">{t("tagNameHint")}</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-muted mb-1">{t("tagDescription")}</label>
          <Textarea
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={t("tagDescriptionPlaceholder")}
            rows={3}
            className="resize-none"
          />
        </div>
        {form.name && (
          <div className="p-2.5 rounded-md bg-secondary border border-border">
            <p className="text-[10px] text-muted mb-1">{t("preview")}</p>
            <code className="text-xs font-mono text-heading">
              [{form.name}:{t("previewValue")}]
            </code>
          </div>
        )}
      </div>
    </Modal>
  );
}
