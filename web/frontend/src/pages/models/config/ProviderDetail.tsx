import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Download, Plus, Save, TestTube } from "lucide-react";
import { providersApi, modelsApi } from "@/lib/api";
import type {
  CreateModelConfig,
  JsonObject,
  ModelConfig,
  ProviderConfig,
  UpdateModelConfig,
  UpdateProviderConfig,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, Input, Select } from "@/components/ui";
import { API_TYPE_OPTIONS, toModelUpdate, type JsonField } from "./shared";
import { ModelCard } from "./ModelCard";
import { ManualAddForm } from "./ManualAddForm";
import { RemoteModelPicker } from "./RemoteModelPicker";

/**
 * 展开的供应商详情：配置编辑 + 模型列表 + 手动/远程添加。
 * 以 key 挂载在供应商卡片内，折叠时卸载，编辑状态自然重置。
 */
export function ProviderDetail({ provider }: { provider: ProviderConfig }) {
  const { t } = useTranslation(["models", "common"]);
  const qc = useQueryClient();
  const pid = provider.id;

  const [providerEdit, setProviderEdit] = useState<UpdateProviderConfig | null>(null);
  const [testResult, setTestResult] = useState("");
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  const [modelEdit, setModelEdit] = useState<UpdateModelConfig | null>(null);
  const [jsonDrafts, setJsonDrafts] = useState<Record<JsonField, string>>({
    request_params: "{}",
    extra_body: "{}",
  });
  const [jsonErrors, setJsonErrors] = useState<Partial<Record<JsonField, string>>>({});
  const [showManualAdd, setShowManualAdd] = useState(false);
  const [showRemote, setShowRemote] = useState(false);
  const [addingRemote, setAddingRemote] = useState(false);

  const { data: providerModels = [] } = useQuery<ModelConfig[]>({
    queryKey: ["providerModels", pid],
    queryFn: () => providersApi.models(pid).then((r) => r.data),
  });

  const invalidateModels = () => {
    qc.invalidateQueries({ queryKey: ["providerModels", pid] });
    qc.invalidateQueries({ queryKey: ["providers"] });
  };

  const updateProviderMut = useMutation({
    mutationFn: (data: UpdateProviderConfig) => providersApi.update(pid, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); setProviderEdit(null); },
  });
  const addModelMut = useMutation({
    mutationFn: (data: CreateModelConfig) => providersApi.createModel(pid, data),
    onSuccess: () => {
      invalidateModels();
      qc.invalidateQueries({ queryKey: ["remoteModels", pid] });
    },
  });
  const updateModelMut = useMutation({
    mutationFn: ({ mid, data }: { mid: string; data: UpdateModelConfig }) => modelsApi.update(mid, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providerModels", pid] }); setModelEdit(null); },
  });
  const removeModelMut = useMutation({
    mutationFn: (mid: string) => modelsApi.remove(mid),
    onSuccess: () => { invalidateModels(); setExpandedModel(null); },
  });

  const pe: ProviderConfig = providerEdit ? { ...provider, ...providerEdit } : provider;

  const handleTest = async () => {
    try {
      const r = await modelsApi.test(provider.base_url, provider.api_key, pid);
      setTestResult(r.data.result);
    } catch { setTestResult(t("connectionFailed")); }
  };

  /** 探测/自动配置前确保编辑草稿存在，并把结果写回草稿 */
  const ensureDraft = (m: ModelConfig): UpdateModelConfig => {
    if (modelEdit) return modelEdit;
    setJsonDrafts({
      request_params: JSON.stringify(m.request_params, null, 2),
      extra_body: JSON.stringify(m.extra_body, null, 2),
    });
    return toModelUpdate(m);
  };

  const handleProbe = async (m: ModelConfig) => {
    try {
      const r = await modelsApi.probe(provider.base_url, provider.api_key, m.model, provider.api_type, pid);
      const d = r.data;
      if (!d.error) {
        const patch: UpdateModelConfig = {
          ...ensureDraft(m),
          supports_vision: d.supports_vision ?? false,
          supports_tools: d.supports_tools ?? false,
        };
        if (d.vision_format) patch.vision_format = d.vision_format;
        setModelEdit(patch);
        setTestResult(t("probeDone") + ": " + JSON.stringify(d));
      } else { setTestResult(t("probeFailed") + ": " + String(d.error)); }
    } catch { setTestResult(t("probeError")); }
  };

  const handleAutoConfig = async (m: ModelConfig) => {
    setTestResult(t("autoConfigLoading"));
    try {
      const r = await providersApi.modelInfo(m.model, provider.api_type);
      const info = r.data;
      if (!info.found) {
        setTestResult(t("autoConfigNotFound"));
        return;
      }
      const patch: UpdateModelConfig = { ...ensureDraft(m) };
      if (info.max_output_tokens) patch.max_tokens = info.max_output_tokens;
      if (info.max_input_tokens) patch.context_window = info.max_input_tokens;
      if (info.supports_vision !== undefined) patch.supports_vision = info.supports_vision;
      if (info.supports_tools !== undefined) patch.supports_tools = info.supports_tools;
      setModelEdit(patch);
      const parts: string[] = [];
      if (info.max_output_tokens) parts.push(`max_tokens=${info.max_output_tokens}`);
      if (info.max_input_tokens) parts.push(`context=${info.max_input_tokens}`);
      if (info.supports_vision) parts.push("vision=true");
      if (info.supports_tools) parts.push("tools=true");
      if (info.input_cost_per_token != null) parts.push(`input=$${(info.input_cost_per_token * 1e6).toFixed(2)}/M`);
      if (info.output_cost_per_token != null) parts.push(`output=$${(info.output_cost_per_token * 1e6).toFixed(2)}/M`);
      setTestResult(t("autoConfigDone") + ": " + parts.join(", "));
    } catch {
      setTestResult(t("autoConfigError"));
    }
  };

  const startModelEdit = (m: ModelConfig) => {
    setModelEdit(toModelUpdate(m));
    setJsonDrafts({
      request_params: JSON.stringify(m.request_params, null, 2),
      extra_body: JSON.stringify(m.extra_body, null, 2),
    });
    setJsonErrors({});
  };

  const parseJsonObject = (field: JsonField): JsonObject | null => {
    try {
      const value: unknown = JSON.parse(jsonDrafts[field]);
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        setJsonErrors((prev) => ({ ...prev, [field]: t("jsonObjectRequired") }));
        return null;
      }
      setJsonErrors((prev) => ({ ...prev, [field]: undefined }));
      return value as JsonObject;
    } catch {
      setJsonErrors((prev) => ({ ...prev, [field]: t("invalidJson") }));
      return null;
    }
  };

  const saveModel = (modelId: string) => {
    if (!modelEdit) return;
    const requestParams = parseJsonObject("request_params");
    const extraBody = parseJsonObject("extra_body");
    if (requestParams === null || extraBody === null) return;
    updateModelMut.mutate({
      mid: modelId,
      data: { ...modelEdit, request_params: requestParams, extra_body: extraBody },
    });
  };

  return (
    <div className="border-t border-border p-3 md:p-4 space-y-4">
      {/* 供应商配置 */}
      <div className="p-4 rounded-md bg-elevated border border-border space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <p className="text-xs font-semibold text-muted uppercase tracking-wider">{t("providerConfig")}</p>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={handleTest}>
              <TestTube size={12} /> {t("common:test")}
            </Button>
            {providerEdit ? (
              <Button variant="primary" size="sm" onClick={() => updateProviderMut.mutate(providerEdit)} loading={updateProviderMut.isPending}>
                <Save size={12} /> {t("common:save")}
              </Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => setProviderEdit({ ...provider })}>
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
                onChange={(e) => providerEdit && setProviderEdit({ ...providerEdit, [k]: e.target.value })}
              />
            </div>
          ))}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("providerFields.api_type", { defaultValue: "api_type" })}</label>
            <Select
              className="w-full"
              value={pe.api_type}
              disabled={!providerEdit}
              onChange={(e) => providerEdit && setProviderEdit({ ...providerEdit, api_type: e.target.value })}
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
              onChange={(e) => providerEdit && setProviderEdit({ ...providerEdit, proxy_url: e.target.value })}
            />
          </div>
        </div>
        {testResult && <div className="p-3 rounded-md bg-card border border-border text-sm text-foreground break-all">{testResult}</div>}
      </div>

      {/* 模型列表操作 */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider">{t("modelList")}</p>
        <div className="flex gap-2">
          <Button
            variant="secondary" size="sm"
            onClick={() => { setShowManualAdd(!showManualAdd); setShowRemote(false); }}
            className={cn(showManualAdd && "border-accent text-accent bg-accent-subtle")}
          >
            <Plus size={12} /> {t("manualAdd")}
          </Button>
          <Button
            variant="secondary" size="sm"
            onClick={() => { setShowRemote(!showRemote); setShowManualAdd(false); }}
            className={cn(showRemote && "border-accent text-accent bg-accent-subtle")}
          >
            <Download size={12} /> {t("browseRemote")}
          </Button>
        </div>
      </div>

      {showManualAdd && (
        <ManualAddForm
          onSubmit={async (data) => { await addModelMut.mutateAsync(data); setShowManualAdd(false); }}
          onCancel={() => setShowManualAdd(false)}
          isPending={addModelMut.isPending}
        />
      )}

      {showRemote && (
        <RemoteModelPicker
          providerId={pid}
          apiType={provider.api_type}
          onAdd={async (data) => { await addModelMut.mutateAsync(data); }}
          isAdding={addingRemote}
          onAddingChange={setAddingRemote}
        />
      )}

      {/* 模型列表 */}
      <div className="space-y-2">
        {providerModels.map((m) => (
          <ModelCard
            key={m.id}
            model={m}
            editing={expandedModel === m.id ? modelEdit : null}
            expanded={expandedModel === m.id}
            onToggle={() => { setExpandedModel(expandedModel === m.id ? null : m.id); setModelEdit(null); setTestResult(""); }}
            onStartEdit={() => startModelEdit(m)}
            onEditChange={(patch) => modelEdit && setModelEdit({ ...modelEdit, ...patch })}
            onSave={() => saveModel(m.id)}
            onProbe={() => handleProbe(m)}
            onAutoConfig={() => handleAutoConfig(m)}
            onRemove={() => removeModelMut.mutate(m.id)}
            jsonDrafts={jsonDrafts}
            onJsonDraftChange={(field, value) => {
              setJsonDrafts((prev) => ({ ...prev, [field]: value }));
              setJsonErrors((prev) => ({ ...prev, [field]: undefined }));
            }}
            jsonErrors={jsonErrors}
            testResult={testResult}
            isPending={updateModelMut.isPending}
          />
        ))}
        {providerModels.length === 0 && <p className="text-sm text-muted py-4 text-center">{t("noModels")}</p>}
      </div>
    </div>
  );
}
