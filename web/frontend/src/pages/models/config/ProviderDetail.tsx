import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Download, Plus } from "lucide-react";
import { providersApi, modelsApi } from "@/lib/api";
import type {
  CreateModelConfig,
  ModelConfig,
  ProviderConfig,
  UpdateModelConfig,
  UpdateProviderConfig,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui";
import { ModelCard } from "./ModelCard";
import { ModelEditorDialog } from "./ModelEditorDialog";
import { ProviderConfigEditor } from "./ProviderConfigEditor";
import { ManualAddForm } from "./ManualAddForm";
import { RemoteModelPicker } from "./RemoteModelPicker";

/**
 * 展开的供应商详情：配置编辑 + 模型列表 + 手动/远程添加。
 * 以 key 挂载在供应商卡片内，折叠时卸载，编辑状态自然重置。
 * 模型编辑在 ModelEditorDialog 中进行；探测/自动配置直接落库。
 */
export function ProviderDetail({ provider }: { provider: ProviderConfig }) {
  const { t } = useTranslation(["models", "common"]);
  const qc = useQueryClient();
  const pid = provider.id;

  const [providerEdit, setProviderEdit] = useState<UpdateProviderConfig | null>(null);
  const [testResult, setTestResult] = useState("");
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  const [editorModel, setEditorModel] = useState<ModelConfig | null>(null);
  const [pendingModelId, setPendingModelId] = useState<string | null>(null);
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
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providerModels", pid] }); setPendingModelId(null); },
  });
  const removeModelMut = useMutation({
    mutationFn: (mid: string) => modelsApi.remove(mid),
    onSuccess: () => { invalidateModels(); setExpandedModel(null); },
  });

  const pe: ProviderConfig = providerEdit ? { ...provider, ...providerEdit } : provider;

  const handleTest = async () => {
    try {
      const r = await modelsApi.test(provider.base_url, provider.api_key, pid, provider.api_type);
      setTestResult(r.data.result);
    } catch { setTestResult(t("connectionFailed")); }
  };

  const handleProbe = async (m: ModelConfig) => {
    setPendingModelId(m.id);
    try {
      const r = await modelsApi.probe(provider.base_url, provider.api_key, m.model, provider.api_type, pid);
      const d = r.data;
      if (!d.error) {
        const patch: UpdateModelConfig = {
          supports_vision: d.supports_vision ?? false,
          supports_tools: d.supports_tools ?? false,
        };
        if (d.vision_format) patch.vision_format = d.vision_format;
        await updateModelMut.mutateAsync({ mid: m.id, data: patch });
        setTestResult(t("probeDone") + ": " + JSON.stringify(d));
      } else { setTestResult(t("probeFailed") + ": " + String(d.error)); }
    } catch { setTestResult(t("probeError")); }
    setPendingModelId(null);
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
      const patch: UpdateModelConfig = {};
      if (info.max_input_tokens) patch.context_window = info.max_input_tokens;
      if (info.supports_vision !== undefined) patch.supports_vision = info.supports_vision;
      if (info.supports_tools !== undefined) patch.supports_tools = info.supports_tools;
      await updateModelMut.mutateAsync({ mid: m.id, data: patch });
      const parts: string[] = [];
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

  return (
    <div className="border-t border-border p-3 md:p-4 space-y-4">
      <ProviderConfigEditor
        provider={provider}
        providerEdit={providerEdit}
        onEditChange={setProviderEdit}
        pe={pe}
        onTest={handleTest}
        onSave={() => providerEdit && updateProviderMut.mutate(providerEdit)}
        savePending={updateProviderMut.isPending}
        testResult={testResult}
      />

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
          existingIds={providerModels.map((m) => m.id)}
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
            expanded={expandedModel === m.id}
            onToggle={() => { setExpandedModel(expandedModel === m.id ? null : m.id); setTestResult(""); }}
            onEdit={() => setEditorModel(m)}
            onProbe={() => handleProbe(m)}
            onAutoConfig={() => handleAutoConfig(m)}
            onRemove={() => removeModelMut.mutate(m.id)}
            testResult={testResult}
            isPending={pendingModelId === m.id && updateModelMut.isPending}
          />
        ))}
        {providerModels.length === 0 && <p className="text-sm text-muted py-4 text-center">{t("noModels")}</p>}
      </div>

      {editorModel && (
        <ModelEditorDialog
          provider={provider}
          model={editorModel}
          onClose={() => setEditorModel(null)}
        />
      )}
    </div>
  );
}
