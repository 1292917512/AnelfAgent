import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Input, Select } from "@/components/ui";
import type { ProviderConfig } from "@/lib/types";
import { ApiTypeSelect, MEDIA_PROTOCOL_OPTIONS } from "./shared";

export interface ProviderFieldsValue {
  name: string; base_url: string; api_key: string; api_type: string; proxy_url: string; media_protocol: string;
}
export const EMPTY_PROVIDER_FIELDS: ProviderFieldsValue = {
  name: "", base_url: "", api_key: "", api_type: "openai", proxy_url: "", media_protocol: "",
};
export function providerFields(provider: ProviderConfig): ProviderFieldsValue {
  return { name: provider.name, base_url: provider.base_url, api_key: provider.api_key, api_type: provider.api_type,
    proxy_url: provider.proxy_url, media_protocol: provider.media_protocol ?? "" };
}

export function ProviderFields({ value, onChange }: { value: ProviderFieldsValue; onChange: (value: ProviderFieldsValue) => void }) {
  const { t } = useTranslation("models");
  const prefix = useId();
  return <div className="grid gap-4 sm:grid-cols-2">
    {(["name", "base_url", "api_key", "proxy_url"] as const).map((key) => <div key={key} className="space-y-1.5">
      <label htmlFor={`${prefix}-${key}`} className="text-xs font-medium text-muted">{t(`providerFields.${key}`)}</label>
      <Input id={`${prefix}-${key}`} type={key === "api_key" ? "password" : "text"} value={value[key]} autoComplete="off"
        placeholder={key === "proxy_url" ? t("proxyPlaceholder") : undefined}
        onChange={(event) => onChange({ ...value, [key]: event.target.value })} />
    </div>)}
    <div className="space-y-1.5">
      <label htmlFor={`${prefix}-api-type`} className="text-xs font-medium text-muted">{t("providerFields.api_type")}</label>
      <ApiTypeSelect id={`${prefix}-api-type`} value={value.api_type} onChange={(api_type, info) =>
        onChange({ ...value, api_type, base_url: value.base_url || info?.default_base_url || "" })} />
    </div>
    <div className="space-y-1.5">
      <label htmlFor={`${prefix}-media`} className="text-xs font-medium text-muted">{t("providerFields.media_protocol")}</label>
      <Select id={`${prefix}-media`} className="w-full" value={value.media_protocol} onChange={(event) => onChange({ ...value, media_protocol: event.target.value })}>
        <option value="">{t("mediaProtocolAuto")}</option>
        {MEDIA_PROTOCOL_OPTIONS.map((protocol) => <option key={protocol} value={protocol}>{protocol}</option>)}
      </Select>
    </div>
  </div>;
}
