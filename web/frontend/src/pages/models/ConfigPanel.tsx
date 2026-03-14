import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { providersApi, modelsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Plus, Trash2, Save, TestTube, Scan, ChevronDown, ChevronRight,
  Eye, Wrench, Server, Brain,
} from "lucide-react";

const API_TYPE_OPTIONS = [
  "openai", "anthropic", "ollama", "gemini", "azure", "deepseek",
  "groq", "bedrock", "vertex_ai", "mistral", "cohere", "huggingface",
  "cloudflare", "openrouter", "together_ai", "fireworks_ai", "perplexity",
  "cerebras", "xai", "sambanova", "volcengine", "dashscope",
];
const MODEL_TYPE_OPTIONS = ["chat", "embedding", "image_gen", "image_edit", "asr", "tts", "video", "rerank"];

interface ProviderInfo {
  id: string; name: string; base_url: string; api_key: string;
  api_type: string; proxy_url: string; model_count: number;
}

interface ModelInfo {
  id: string; name: string; model: string; model_types: string[];
  supports_vision: boolean; supports_tools: boolean; vision_format: string;
  temperature: number; top_p: number; max_tokens: number;
  frequency_penalty: number; presence_penalty: number; timeout: number;
  is_default: boolean; supports_reasoning: boolean;
}

export function ConfigPanel() {
  const { t } = useTranslation(["models", "common"]);
  const qc = useQueryClient();
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  const [providerEdit, setProviderEdit] = useState<Record<string, unknown> | null>(null);
  const [modelEdit, setModelEdit] = useState<Record<string, unknown> | null>(null);
  const [newProvider, setNewProvider] = useState({ id: "", name: "", base_url: "", api_key: "", api_type: "openai", proxy_url: "" });
  const [newModel, setNewModel] = useState({ id: "", model: "" });
  const [showNewProvider, setShowNewProvider] = useState(false);
  const [addingModelTo, setAddingModelTo] = useState<string | null>(null);
  const [testResult, setTestResult] = useState("");

  const { data: providers = [] } = useQuery<ProviderInfo[]>({
    queryKey: ["providers"],
    queryFn: () => providersApi.list().then(r => r.data),
  });

  const { data: providerModels = [] } = useQuery<ModelInfo[]>({
    queryKey: ["providerModels", expandedProvider],
    queryFn: () => expandedProvider ? providersApi.models(expandedProvider).then(r => r.data) : Promise.resolve([]),
    enabled: !!expandedProvider,
  });

  const addProviderMut = useMutation({
    mutationFn: (data: Record<string, unknown>) => providersApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); setShowNewProvider(false); setNewProvider({ id: "", name: "", base_url: "", api_key: "", api_type: "openai", proxy_url: "" }); },
  });
  const updateProviderMut = useMutation({
    mutationFn: ({ pid, data }: { pid: string; data: Record<string, unknown> }) => providersApi.update(pid, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); setProviderEdit(null); },
  });
  const removeProviderMut = useMutation({
    mutationFn: (pid: string) => providersApi.remove(pid),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); setExpandedProvider(null); },
  });
  const addModelMut = useMutation({
    mutationFn: ({ pid, data }: { pid: string; data: Record<string, unknown> }) => providersApi.createModel(pid, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providerModels", expandedProvider] }); qc.invalidateQueries({ queryKey: ["providers"] }); setAddingModelTo(null); setNewModel({ id: "", model: "" }); },
  });
  const updateModelMut = useMutation({
    mutationFn: ({ mid, data }: { mid: string; data: Record<string, unknown> }) => modelsApi.update(mid, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providerModels", expandedProvider] }); setModelEdit(null); },
  });
  const removeModelMut = useMutation({
    mutationFn: (mid: string) => modelsApi.remove(mid),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providerModels", expandedProvider] }); qc.invalidateQueries({ queryKey: ["providers"] }); setExpandedModel(null); },
  });

  const toggleProvider = (pid: string) => {
    setExpandedProvider(expandedProvider === pid ? null : pid);
    setExpandedModel(null); setProviderEdit(null); setModelEdit(null); setTestResult("");
  };

  const handleProbe = async (m: ModelInfo, prov: ProviderInfo) => {
    try {
      const r = await modelsApi.probe(prov.base_url, prov.api_key, m.model, prov.api_type);
      const d = r.data as Record<string, unknown>;
      if (!d.error) {
        setModelEdit({ ...(modelEdit ?? { ...m }), supports_vision: d.supports_vision ?? false, supports_tools: d.supports_tools ?? false });
        setTestResult(t("probeDone") + ": " + JSON.stringify(d));
      } else { setTestResult(t("probeFailed") + ": " + String(d.error)); }
    } catch { setTestResult(t("probeError")); }
  };

  const currentProvider = providers.find(p => p.id === expandedProvider);
  const editableModelFields = ["model", "temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty", "timeout"] as const;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--text-strong)]">{t("providersAndModels")}</h3>
        <button onClick={() => setShowNewProvider(!showNewProvider)}
          className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] transition-all">
          <Plus size={16} /> {t("addProvider")}
        </button>
      </div>

      {showNewProvider && (
        <div className="p-4 rounded-[var(--radius-md)] border border-[var(--accent)] bg-[var(--card)] space-y-3">
          <p className="text-sm font-semibold text-[var(--text-strong)]">{t("newProvider")}</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {(["id", "name", "base_url", "api_key"] as const).map(k => (
              <div key={k} className="space-y-1">
                <label className="text-xs font-medium text-[var(--muted)]">{t(`providerFields.${k}`, { defaultValue: k })}</label>
                <input type={k === "api_key" ? "password" : "text"}
                  value={newProvider[k]} onChange={e => setNewProvider({ ...newProvider, [k]: e.target.value })}
                  className="w-full bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
              </div>
            ))}
            <div className="space-y-1">
              <label className="text-xs font-medium text-[var(--muted)]">{t("providerFields.api_type", { defaultValue: "api_type" })}</label>
              <select value={newProvider.api_type} onChange={e => setNewProvider({ ...newProvider, api_type: e.target.value })}
                className="w-full bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]">
                {API_TYPE_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-[var(--muted)]">{t("providerFields.proxy_url", { defaultValue: "proxy_url" })}</label>
              <input type="text" placeholder={t("proxyPlaceholder")}
                value={newProvider.proxy_url} onChange={e => setNewProvider({ ...newProvider, proxy_url: e.target.value })}
                className="w-full bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => newProvider.id && addProviderMut.mutate(newProvider)} disabled={!newProvider.id}
              className="px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-all">{t("common:create")}</button>
            <button onClick={() => setShowNewProvider(false)}
              className="px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all">{t("common:cancel")}</button>
          </div>
        </div>
      )}

      <div className="grid gap-3">
        {providers.map(prov => {
          const isOpen = expandedProvider === prov.id;
          const pe = providerEdit && isOpen ? providerEdit : prov;
          return (
            <div key={prov.id} className={cn(
              "rounded-[var(--radius-md)] border transition-all bg-[var(--card)]",
              isOpen ? "border-[var(--accent)] shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]" : "border-[var(--border)] hover:border-[var(--border-strong)]",
            )}>
              <div className="flex items-center justify-between p-4 cursor-pointer" onClick={() => toggleProvider(prov.id)}>
                <div className="flex items-center gap-3">
                  {isOpen ? <ChevronDown size={16} className="text-[var(--accent)]" /> : <ChevronRight size={16} className="text-[var(--muted)]" />}
                  <Server size={16} className="text-[var(--accent)]" />
                  <span className="font-medium text-[var(--text-strong)]">{prov.name || prov.id}</span>
                  <span className="text-xs text-[var(--muted)]">{t("nModels", { count: prov.model_count })}</span>
                </div>
                <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                  <button onClick={() => removeProviderMut.mutate(prov.id)}
                    className="p-1.5 rounded text-[var(--muted)] hover:text-[var(--danger)] transition-colors" title={t("deleteProvider")}><Trash2 size={14} /></button>
                </div>
              </div>

              {isOpen && (
                <div className="border-t border-[var(--border)] p-4 space-y-4">
                  <div className="p-4 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)] space-y-3">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">{t("providerConfig")}</p>
                      <div className="flex gap-2">
                        <button onClick={async () => { try { const r = await modelsApi.test(prov.base_url, prov.api_key); setTestResult(r.data.result); } catch { setTestResult(t("connectionFailed")); } }}
                          className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all">
                          <TestTube size={12} /> {t("common:test")}</button>
                        {providerEdit ? (
                          <button onClick={() => updateProviderMut.mutate({ pid: prov.id, data: providerEdit })}
                            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] transition-all">
                            <Save size={12} /> {t("common:save")}</button>
                        ) : (
                          <button onClick={() => setProviderEdit({ ...prov })}
                            className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all">{t("common:edit")}</button>
                        )}
                      </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {(["name", "base_url", "api_key"] as const).map(k => (
                        <div key={k} className="space-y-1">
                          <label className="text-xs font-medium text-[var(--muted)]">{t(`providerFields.${k}`, { defaultValue: k })}</label>
                          <input type={k === "api_key" ? "password" : "text"}
                            value={String((pe as Record<string, unknown>)[k] ?? "")} readOnly={!providerEdit}
                            onChange={e => providerEdit && setProviderEdit({ ...providerEdit, [k]: e.target.value })}
                            className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
                        </div>
                      ))}
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-[var(--muted)]">{t("providerFields.api_type", { defaultValue: "api_type" })}</label>
                        <select value={String((pe as Record<string, unknown>).api_type ?? "openai")} disabled={!providerEdit}
                          onChange={e => providerEdit && setProviderEdit({ ...providerEdit, api_type: e.target.value })}
                          className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]">
                          {API_TYPE_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                        </select>
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-[var(--muted)]">{t("providerFields.proxy_url", { defaultValue: "proxy_url" })}</label>
                        <input type="text" placeholder={t("proxyPlaceholder")}
                          value={String((pe as Record<string, unknown>).proxy_url ?? "")} readOnly={!providerEdit}
                          onChange={e => providerEdit && setProviderEdit({ ...providerEdit, proxy_url: e.target.value })}
                          className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
                      </div>
                    </div>
                    {testResult && <div className="p-3 rounded-[var(--radius-md)] bg-[var(--card)] border border-[var(--border)] text-sm text-[var(--text)]">{testResult}</div>}
                  </div>

                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">{t("modelList")}</p>
                    <button onClick={() => { setAddingModelTo(addingModelTo === prov.id ? null : prov.id); setNewModel({ id: "", model: "" }); }}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] transition-all">
                      <Plus size={12} /> {t("addModel")}</button>
                  </div>

                  {addingModelTo === prov.id && (
                    <div className="p-3 rounded-[var(--radius-md)] border border-[var(--accent)] bg-[var(--bg-elevated)] space-y-2">
                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                          <label className="text-xs font-medium text-[var(--muted)]">{t("modelId")}</label>
                          <input value={newModel.id} onChange={e => setNewModel({ ...newModel, id: e.target.value })}
                            className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
                        </div>
                        <div className="space-y-1">
                          <label className="text-xs font-medium text-[var(--muted)]">{t("modelName")}</label>
                          <input value={newModel.model} onChange={e => setNewModel({ ...newModel, model: e.target.value })}
                            className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <button onClick={() => newModel.id && addModelMut.mutate({ pid: prov.id, data: newModel })} disabled={!newModel.id}
                          className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-all">{t("common:create")}</button>
                        <button onClick={() => setAddingModelTo(null)}
                          className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all">{t("common:cancel")}</button>
                      </div>
                    </div>
                  )}

                  <div className="space-y-2">
                    {providerModels.map(m => {
                      const isModelOpen = expandedModel === m.id;
                      const me = modelEdit && isModelOpen ? modelEdit : m;
                      return (
                        <div key={m.id} className={cn(
                          "rounded-[var(--radius-md)] border transition-all",
                          isModelOpen ? "border-[var(--accent-2)] bg-[var(--bg-elevated)]" : "border-[var(--border)] bg-[var(--bg-elevated)] hover:border-[var(--border-strong)]",
                        )}>
                          <div className="flex items-center justify-between p-3 cursor-pointer"
                            onClick={() => { setExpandedModel(isModelOpen ? null : m.id); setModelEdit(null); setTestResult(""); }}>
                            <div className="flex items-center gap-2">
                              {isModelOpen ? <ChevronDown size={14} className="text-[var(--accent-2)]" /> : <ChevronRight size={14} className="text-[var(--muted)]" />}
                              <span className="text-sm font-medium text-[var(--text-strong)]">{m.id}</span>
                              <span className="text-xs text-[var(--muted)]">{m.model}</span>
                              <div className="flex gap-1 ml-1">
                                {m.supports_vision && <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent-2-subtle)] text-[var(--accent-2)] border border-[rgba(20,184,166,0.3)]"><Eye size={9} /> {t("vision")}</span>}
                                {m.supports_tools && <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent-subtle)] text-[var(--accent)] border border-[rgba(74,144,217,0.3)]"><Wrench size={9} /> {t("toolCall")}</span>}
                                {m.supports_reasoning && <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(168,85,247,0.1)] text-[rgb(168,85,247)] border border-[rgba(168,85,247,0.3)]"><Brain size={9} /> {t("deepThinking")}</span>}
                                {m.model_types.map(mt => <span key={mt} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)]">{t(`modelTypeLabels.${mt}`, { defaultValue: mt })}</span>)}
                              </div>
                            </div>
                            <button onClick={e => { e.stopPropagation(); removeModelMut.mutate(m.id); }}
                              className="p-1 rounded text-[var(--muted)] hover:text-[var(--danger)] transition-colors"><Trash2 size={13} /></button>
                          </div>
                          {isModelOpen && (
                            <div className="border-t border-[var(--border)] p-3 space-y-3">
                              <div className="flex gap-2">
                                {currentProvider && <button onClick={() => handleProbe(m, currentProvider)}
                                  className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"><Scan size={12} /> {t("probeCapability")}</button>}
                                {modelEdit ? (
                                  <button onClick={() => updateModelMut.mutate({ mid: m.id, data: modelEdit })}
                                    className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] transition-all"><Save size={12} /> {t("common:save")}</button>
                                ) : (
                                  <button onClick={() => setModelEdit({ ...m })}
                                    className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all">{t("common:edit")}</button>
                                )}
                              </div>
                              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                {editableModelFields.map(k => (
                                  <div key={k} className="space-y-1">
                                    <label className="text-xs font-medium text-[var(--muted)]">{k}</label>
                                    <input value={String((me as Record<string, unknown>)[k] ?? "")} readOnly={!modelEdit}
                                      onChange={e => modelEdit && setModelEdit({ ...modelEdit, [k]: e.target.value })}
                                      className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-1.5 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
                                  </div>
                                ))}
                              </div>
                              <div className="flex flex-wrap gap-3">
                                <label className="flex items-center gap-2 cursor-pointer">
                                  <input type="checkbox" checked={!!(me as Record<string, unknown>).supports_vision} disabled={!modelEdit}
                                    onChange={e => modelEdit && setModelEdit({ ...modelEdit, supports_vision: e.target.checked })}
                                    className="accent-[var(--accent-2)] w-3.5 h-3.5" />
                                  <span className="text-xs text-[var(--text)]">{t("vision")}</span>
                                </label>
                                {!!(me as Record<string, unknown>).supports_vision && (
                                  <select value={String((me as Record<string, unknown>).vision_format ?? "base64")} disabled={!modelEdit}
                                    onChange={e => modelEdit && setModelEdit({ ...modelEdit, vision_format: e.target.value })}
                                    className="bg-[var(--card)] border border-[var(--input)] rounded px-2 py-0.5 text-xs text-[var(--text)] outline-none">
                                    <option value="base64">base64</option><option value="url">url</option><option value="both">both</option>
                                  </select>
                                )}
                                <label className="flex items-center gap-2 cursor-pointer">
                                  <input type="checkbox" checked={!!(me as Record<string, unknown>).supports_tools} disabled={!modelEdit}
                                    onChange={e => modelEdit && setModelEdit({ ...modelEdit, supports_tools: e.target.checked })}
                                    className="accent-[var(--accent)] w-3.5 h-3.5" />
                                  <span className="text-xs text-[var(--text)]">{t("toolCall")}</span>
                                </label>
                                <label className="flex items-center gap-2 cursor-pointer">
                                  <input type="checkbox" checked={!!(me as Record<string, unknown>).supports_reasoning} disabled={!modelEdit}
                                    onChange={e => modelEdit && setModelEdit({ ...modelEdit, supports_reasoning: e.target.checked })}
                                    className="accent-[rgb(168,85,247)] w-3.5 h-3.5" />
                                  <span className="text-xs text-[var(--text)]">{t("deepThinking")}</span>
                                </label>
                              </div>
                              <div>
                                <p className="text-xs font-medium text-[var(--muted)] mb-1">{t("modelTypes")}</p>
                                <div className="flex flex-wrap gap-1.5">
                                  {MODEL_TYPE_OPTIONS.map(mt => {
                                    const types = (Array.isArray((me as Record<string, unknown>).model_types) ? (me as Record<string, unknown>).model_types : []) as string[];
                                    const active = types.includes(mt);
                                    return (
                                      <button key={mt} disabled={!modelEdit}
                                        onClick={() => { if (!modelEdit) return; const cur = (Array.isArray(modelEdit.model_types) ? modelEdit.model_types : []) as string[]; setModelEdit({ ...modelEdit, model_types: active ? cur.filter(x => x !== mt) : [...cur, mt] }); }}
                                        className={cn("px-2.5 py-0.5 text-xs font-medium rounded-full border transition-all",
                                          active ? "bg-[var(--accent-subtle)] text-[var(--accent)] border-[var(--accent)]" : "bg-[var(--secondary)] text-[var(--muted)] border-[var(--border)]",
                                          !modelEdit && "opacity-60 cursor-default")}>{t(`modelTypeLabels.${mt}`, { defaultValue: mt })}</button>
                                    );
                                  })}
                                </div>
                              </div>
                              {testResult && <div className="p-2 rounded bg-[var(--card)] border border-[var(--border)] text-xs text-[var(--text)]">{testResult}</div>}
                            </div>
                          )}
                        </div>
                      );
                    })}
                    {providerModels.length === 0 && <p className="text-sm text-[var(--muted)] py-4 text-center">{t("noModels")}</p>}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {providers.length === 0 && !showNewProvider && <p className="text-sm text-[var(--muted)] py-8 text-center">{t("noProviders")}</p>}
      </div>
    </div>
  );
}
