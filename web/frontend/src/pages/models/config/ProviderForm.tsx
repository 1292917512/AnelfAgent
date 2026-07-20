import { useState } from "react";
import axios from "axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { providersApi } from "@/lib/api";
import type { CreateProviderConfig } from "@/lib/types";
import { Button, Input, Select } from "@/components/ui";
import { API_TYPE_OPTIONS } from "./shared";

const EMPTY_PROVIDER: CreateProviderConfig = {
  id: "", name: "", base_url: "", api_key: "", api_type: "openai", proxy_url: "",
};

/** 新建供应商表单 */
export function ProviderForm({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation(["models", "common"]);
  const qc = useQueryClient();
  const [form, setForm] = useState<CreateProviderConfig>(EMPTY_PROVIDER);
  const [error, setError] = useState("");

  const addMut = useMutation({
    mutationFn: (data: CreateProviderConfig) => providersApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      onClose();
    },
    onError: (err) => {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null;
      setError(`${t("createProviderFailed")}: ${typeof detail === "string" ? detail : String(err)}`);
    },
  });

  return (
    <div className="p-4 rounded-md border border-accent bg-card space-y-3">
      <p className="text-sm font-semibold text-heading">{t("newProvider")}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {(["id", "name", "base_url", "api_key"] as const).map((k) => (
          <div key={k} className="space-y-1">
            <label className="text-xs font-medium text-muted">{t(`providerFields.${k}`, { defaultValue: k })}</label>
            <Input
              type={k === "api_key" ? "password" : "text"}
              value={form[k]}
              onChange={(e) => setForm({ ...form, [k]: e.target.value })}
            />
          </div>
        ))}
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("providerFields.api_type", { defaultValue: "api_type" })}</label>
          <Select className="w-full" value={form.api_type} onChange={(e) => setForm({ ...form, api_type: e.target.value })}>
            {API_TYPE_OPTIONS.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("providerFields.proxy_url", { defaultValue: "proxy_url" })}</label>
          <Input
            type="text"
            placeholder={t("proxyPlaceholder")}
            value={form.proxy_url}
            onChange={(e) => setForm({ ...form, proxy_url: e.target.value })}
          />
        </div>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      <div className="flex gap-2">
        <Button
          variant="primary"
          onClick={() => form.id.trim() && addMut.mutate({ ...form, id: form.id.trim() })}
          disabled={!form.id.trim()}
          loading={addMut.isPending}
        >
          <Plus size={14} /> {t("common:create")}
        </Button>
        <Button variant="secondary" onClick={onClose}>{t("common:cancel")}</Button>
      </div>
    </div>
  );
}
