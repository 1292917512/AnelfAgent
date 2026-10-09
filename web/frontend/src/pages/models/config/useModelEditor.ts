import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { modelsApi, providersApi } from "@/lib/api";
import type { ModelConfig, ProviderConfig, TestChatResult, UpdateModelConfig } from "@/lib/types";
import { highestContractEffort } from "@/components/common/ReasoningEffortSelect";
import { parseBuiltinTools, parseHeaders, parseJsonObject } from "./model-editor-data";

type JsonField = "request_params" | "extra_body" | "extra_headers" | "thinking";
export interface ModelTestState { status: "idle" | "running" | "success" | "error"; result?: TestChatResult; snapshot?: string }

export function useModelEditor(provider: ProviderConfig, model: ModelConfig, onSaved: () => void) {
  const { t } = useTranslation("models");
  const client = useQueryClient();
  const [draft, setDraft] = useState<UpdateModelConfig>(() => ({
    model: model.model,
    context_window: model.context_window ?? 0,
    max_tokens: model.max_tokens ?? null,
    temperature: model.temperature,
    top_p: model.top_p ?? null,
    chat_protocol: model.chat_protocol ?? "chat_completions",
    supports_vision: model.supports_vision,
    supports_video: model.supports_video,
    vision_format: model.vision_format,
    supports_tools: model.supports_tools,
    supports_forced_tool_choice: model.supports_forced_tool_choice,
    supports_reasoning: model.supports_reasoning,
    // 历史空档（跟随已移除的全局配置）按模型思考契约最高档归一，保存时固化
    reasoning_effort: model.supports_reasoning
      ? model.reasoning_effort || highestContractEffort(model.thinking)
      : model.reasoning_effort ?? "",
    model_types: model.model_types,
    enabled: model.enabled,
  }));
  const patch = (p: Partial<UpdateModelConfig>) => setDraft((d) => ({ ...d, ...p }));

  const [jsonDrafts, setJsonDrafts] = useState<Record<JsonField, string>>(() => ({
    request_params: JSON.stringify(model.request_params ?? {}, null, 2),
    extra_body: JSON.stringify(model.extra_body ?? {}, null, 2),
    extra_headers: JSON.stringify(model.extra_headers ?? {}, null, 2),
    thinking: JSON.stringify(model.thinking ?? {}, null, 2),
  }));
  const [jsonEnabled, setJsonEnabled] = useState<Record<JsonField, boolean>>(() => ({
    request_params: Object.keys(model.request_params ?? {}).length > 0,
    extra_body: Object.keys(model.extra_body ?? {}).length > 0,
    extra_headers: Object.keys(model.extra_headers ?? {}).length > 0,
    thinking: Object.keys(model.thinking ?? {}).length > 0,
  }));
  const [jsonErrors, setJsonErrors] = useState<Partial<Record<JsonField, string>>>({});
  const [builtinToolsText, setBuiltinToolsText] = useState(() => (model.builtin_tools ?? [])
    .map((item) => typeof item === "string" ? item : JSON.stringify(item)).join(", "));
  const [builtinToolsError, setBuiltinToolsError] = useState<string>();
  const snapshot = JSON.stringify({ draft, jsonDrafts, jsonEnabled, builtinToolsText });
  const [savedSnapshot, setSavedSnapshot] = useState(snapshot);
  const [test, setTest] = useState<ModelTestState>({ status: "idle" });
  const remoteQuery = useQuery({ queryKey: ["remoteModels", provider.id],
    queryFn: () => providersApi.remoteModels(provider.id).then((response) => response.data.models),
    staleTime: 60_000, retry: false, throwOnError: false });
  const update = useMutation({
    mutationFn: (payload: UpdateModelConfig) => modelsApi.update(model.id, payload),
    onSuccess: async () => {
      await Promise.all([client.invalidateQueries({ queryKey: ["providerModels", provider.id] }),
        client.invalidateQueries({ queryKey: ["providers"] }), client.invalidateQueries({ queryKey: ["priorities"] })]);
    },
  });
  const busy = update.isPending || test.status === "running";
  const buildPayload = (): UpdateModelConfig | null => {
    const errors: Partial<Record<JsonField, string>> = {};
    const payload: UpdateModelConfig = { ...draft };
    for (const field of ["request_params", "extra_body", "extra_headers", "thinking"] as const) {
      try {
        const text = jsonEnabled[field] ? jsonDrafts[field] : "{}";
        if (field === "extra_headers") payload.extra_headers = parseHeaders(text);
        else payload[field] = parseJsonObject(text);
      } catch (error) {
        errors[field] = t(error instanceof SyntaxError ? "invalidJson" : error instanceof Error ? error.message : "invalidJson");
      }
    }
    setJsonErrors(errors);
    let builtinError: string | undefined;
    try { payload.builtin_tools = parseBuiltinTools(builtinToolsText); }
    catch (error) { builtinError = t(error instanceof SyntaxError ? "invalidJson" : error instanceof Error ? error.message : "invalidJson"); }
    setBuiltinToolsError(builtinError);
    return Object.keys(errors).length || builtinError ? null : payload;
  };
  const save = async (withTest: boolean) => {
    if (busy) return;
    const payload = buildPayload();
    if (!payload) return;
    const submitted = snapshot;
    try { await update.mutateAsync(payload); }
    catch { return; }
    setSavedSnapshot(submitted);
    if (!withTest) { onSaved(); return; }
    setTest({ status: "running", snapshot: submitted });
    try {
      const { data } = await modelsApi.testChat(provider.id, model.id);
      setTest({ status: data.ok ? "success" : "error", result: data, snapshot: submitted });
    } catch (error) {
      setTest({ status: "error", snapshot: submitted, result: { ok: false, error: error instanceof Error ? error.message : String(error) } });
    }
  };
  return { draft, patch, jsonDrafts, setJsonDrafts, jsonEnabled, setJsonEnabled, jsonErrors, setJsonErrors,
    builtinToolsText, setBuiltinToolsText, builtinToolsError, setBuiltinToolsError, remoteQuery,
    remoteIds: (remoteQuery.data ?? []).map((item) => item.id), test, busy, save, error: update.error,
    dirty: snapshot !== savedSnapshot, stale: test.snapshot !== undefined && test.snapshot !== snapshot };
}
export type ModelEditorState = ReturnType<typeof useModelEditor>;
