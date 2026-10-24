import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/Modal";
import type { ConfigValues } from "@/lib/types";
import { AppField, type FieldMeta } from "./AppField";

/** Metadata-driven fields with optional confirmation before changing a switch draft. */
export function ConfigFields({ fields, values, onChange, advancedLabel, confirmBooleanChanges = false, note }: {
  fields: FieldMeta[]; values: ConfigValues; onChange: (key: string, value: unknown) => void;
  advancedLabel?: string; confirmBooleanChanges?: boolean; note?: string;
}) {
  const { t } = useTranslation("appconfig");
  const [pendingToggle, setPendingToggle] = useState<{ field: FieldMeta; value: boolean } | null>(null);
  const changeField = (field: FieldMeta, value: unknown) => {
    if (confirmBooleanChanges && field.type === "bool" && typeof value === "boolean") {
      setPendingToggle({ field, value });
    } else onChange(field.key, value);
  };
  return <>
    <div className={fields.filter(field => !field.advanced).length > 1 ? "grid grid-cols-1 gap-4 md:grid-cols-2" : "grid grid-cols-1 gap-4"}>
      {fields.filter((field) => !field.advanced).map((field) => <AppField key={field.key} meta={field} value={values[field.key]} onChange={(value) => changeField(field, value)} />)}
    </div>
    {fields.some((field) => field.advanced) && <details className="mt-4 rounded-xl border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium text-heading">{advancedLabel}</summary>
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        {fields.filter((field) => field.advanced).map((field) => <AppField key={field.key} meta={field} value={values[field.key]} onChange={(value) => changeField(field, value)} />)}
      </div>
    </details>}
    <ConfirmDialog open={pendingToggle !== null} onClose={() => setPendingToggle(null)}
      title={t(pendingToggle?.value ? "toggleConfirm.enable" : "toggleConfirm.disable", { ns: "appconfig", name: pendingToggle?.field.label })}
      message={<div className="space-y-3">
        {pendingToggle?.field.desc && <p>{pendingToggle.field.desc}</p>}
        <p className="text-muted">{t("toggleConfirm.draftNote", { ns: "appconfig" })}</p>
        {note && <p className="text-muted">{note}</p>}
      </div>}
      onConfirm={() => {
        if (pendingToggle && fields.some((field) => field.key === pendingToggle.field.key && !field.read_only)) {
          onChange(pendingToggle.field.key, pendingToggle.value);
        }
        setPendingToggle(null);
      }} />
  </>;
}
