import { useId } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { Button, Input } from "@/components/ui";

export function ModelCombobox({ value, onChange, options, loading, onFetch, placeholder }: {
  value: string; onChange: (value: string) => void; options: string[]; loading: boolean;
  onFetch: () => void; placeholder?: string;
}) {
  const { t } = useTranslation("models");
  const id = useId();
  return <div className="flex gap-2">
    <Input aria-label={t("modelIdLabel")} list={id} value={value} onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder} autoComplete="off" />
    <datalist id={id}>{options.map((option) => <option key={option} value={option} />)}</datalist>
    <Button variant="secondary" size="icon" title={t("fetchModels")} onClick={onFetch} loading={loading} className="shrink-0"><RefreshCw size={14} /></Button>
  </div>;
}
