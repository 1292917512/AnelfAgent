import { useTranslation } from "react-i18next";
import { Save, TestTube } from "lucide-react";
import type { ProviderConfig, UpdateProviderConfig } from "@/lib/types";
import { Button, Input, Select } from "@/components/ui";
import { API_TYPE_OPTIONS, MEDIA_PROTOCOL_OPTIONS } from "./shared";

/** 供应商配置编辑块：名称 / base_url / api_key / api_type / 代理 / 媒体协议 + 测试 */
export function ProviderConfigEditor({
  provider,
  providerEdit,
  onEditChange,
  pe,
  onTest,
  onSave,
  savePending,
  testResult,
}: {
  provider: ProviderConfig;
  providerEdit: UpdateProviderConfig | null;
  onEditChange: (v: UpdateProviderConfig | null) => void;
  pe: {
    name: string;
    base_url: string;
    api_key: string;
    api_type: string;
    proxy_url: string;
    media_protocol: string | undefined;
  };
  onTest: () => void;
  onSave: () => void;
  savePending: boolean;
  testResult: string;
}) {
  const { t } = useTranslation("models");
  return (
    <div className="p-4 rounded-md bg-elevated border border-border space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider">{t("providerConfig")}</p>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={onTest}>
            <TestTube size={12} /> {t("common:test")}
          </Button>
          {providerEdit ? (
            <Button variant="primary" size="sm" onClick={onSave} loading={savePending}>
              <Save size={12} /> {t("common:save")}
            </Button>
          ) : (
            <Button variant="secondary" size="sm" onClick={() => onEditChange({ ...provider })}>
              {t("common:edit")}
            </Button>
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {(["name", "base_url", "api_key"] as const).map((k) => (
          <div key={k} className="space-y-1">
            <label className="text-xs font-medium text-muted">{t(`providerFields.${k}`, { defaultValue: k })}</label>
            <Input
              type={k === "api_key" ? "password" : "text"}
              value={pe[k]}
              readOnly={!providerEdit}
              onChange={(e) => providerEdit && onEditChange({ ...providerEdit, [k]: e.target.value })}
            />
          </div>
        ))}
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("providerFields.api_type", { defaultValue: "api_type" })}</label>
          <Select
            className="w-full"
            value={pe.api_type}
            disabled={!providerEdit}
            onChange={(e) => providerEdit && onEditChange({ ...providerEdit, api_type: e.target.value })}
          >
            {API_TYPE_OPTIONS.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("providerFields.proxy_url", { defaultValue: "proxy_url" })}</label>
          <Input
            type="text"
            placeholder={t("proxyPlaceholder")}
            value={pe.proxy_url}
            readOnly={!providerEdit}
            onChange={(e) => providerEdit && onEditChange({ ...providerEdit, proxy_url: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("providerFields.media_protocol", { defaultValue: "media_protocol" })}</label>
          <Select
            className="w-full"
            value={pe.media_protocol ?? ""}
            disabled={!providerEdit}
            onChange={(e) => providerEdit && onEditChange({ ...providerEdit, media_protocol: e.target.value })}
          >
            <option value="">{t("mediaProtocolAuto")}</option>
            {MEDIA_PROTOCOL_OPTIONS.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
          </Select>
        </div>
      </div>
      {testResult && <div className="p-3 rounded-md bg-card border border-border text-sm text-foreground break-all">{testResult}</div>}
    </div>
  );
}
