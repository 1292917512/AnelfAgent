import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  FlaskConical,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { modelsApi, providersApi } from "@/lib/api";
import type {
  JsonObject,
  ModelConfig,
  ProviderConfig,
  TestChatResult,
  UpdateModelConfig,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, Input, Modal, Select, Switch, Textarea } from "@/components/ui";
import { ReasoningEffortOptions } from "@/components/common/ReasoningEffortSelect";
import { MODEL_TYPE_OPTIONS } from "./shared";

type JsonField = "request_params" | "extra_body" | "extra_headers" | "thinking";

/** FNV-1a 配置指纹：判断测试结果是否因配置变更而过期 */
function fnv1a(str: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16);
}

interface TestState {
  status: "idle" | "running" | "success" | "error";
  result?: TestChatResult;
  /** 测试通过/失败时的配置指纹 */
  hash?: string;
}

/** 模型标识 Combobox：可手输可下拉，内嵌「获取模型」按钮 */
function ModelCombobox({
  value,
  onChange,
  options,
  loading,
  onFetch,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  loading: boolean;
  onFetch: () => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const filtered = useMemo(() => {
    const kw = value.trim().toLowerCase();
    const list = kw ? options.filter((o) => o.toLowerCase().includes(kw)) : options;
    return list.slice(0, 100);
  }, [options, value]);

  return (
    <div ref={ref} className="relative">
      <div className="flex gap-1.5">
        <div className="relative flex-1">
          <Input
            value={value}
            placeholder={placeholder}
            onChange={(e) => onChange(e.target.value)}
            onFocus={() => setOpen(true)}
            className="pr-7"
          />
          <ChevronDown
            size={13}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
          />
        </div>
        <Button variant="secondary" size="sm" onClick={onFetch} loading={loading} className="shrink-0">
          {!loading && <RefreshCw size={12} />}
        </Button>
      </div>
      {open && filtered.length > 0 && (
        <div className="absolute z-20 mt-1 w-full max-h-52 overflow-y-auto rounded-md border border-border bg-card shadow-lg">
          {filtered.map((id) => (
            <button
              key={id}
              type="button"
              className={cn(
                "w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-accent-subtle truncate",
                id === value ? "text-accent" : "text-foreground",
              )}
              onMouseDown={(e) => {
                e.preventDefault();
                onChange(id);
                setOpen(false);
              }}
            >
              {id}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 可勾选启用的 JSON 编辑区块 */
function JsonSection({
  label,
  hint,
  enabled,
  onEnabledChange,
  value,
  onChange,
  error,
}: {
  label: string;
  hint: string;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  const { t } = useTranslation("models");
  return (
    <div className={cn("rounded-md border p-3 space-y-2", enabled ? "border-accent2/50" : "border-border")}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted" title={hint}>{label}</span>
        <label className="flex items-center gap-1.5 cursor-pointer text-xs text-muted">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onEnabledChange(e.target.checked)}
            className="accent-accent2 w-3.5 h-3.5"
          />
          {t("enableLabel")}
        </label>
      </div>
      {enabled && (
        <>
          <Textarea
            rows={4}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className={cn("font-mono text-xs", error && "border-danger")}
            spellCheck={false}
          />
          <p className={cn("text-[11px]", error ? "text-danger" : "text-muted opacity-70")}>
            {error ?? hint}
          </p>
        </>
      )}
    </div>
  );
}

/**
 * 模型编辑对话框：
 * 模型标识 Combobox + 获取模型、思考强度独立下拉、接口端点选择、
 * 额外参数/请求头勾选启用、保存并测试（真实链路）+ 配置变更过期检测。
 */
export function ModelEditorDialog({
  provider,
  model,
  onClose,
}: {
  provider: ProviderConfig;
  model: ModelConfig;
  onClose: () => void;
}) {
  const { t } = useTranslation(["models", "common"]);
  const qc = useQueryClient();
  const pid = provider.id;

  // ── 草稿 ──────────────────────────────────────────────────────────
  const [draft, setDraft] = useState<UpdateModelConfig>(() => ({
    model: model.model,
    context_window: model.context_window ?? 0,
    max_tokens: model.max_tokens ?? null,
    temperature: model.temperature,
    top_p: model.top_p ?? null,
    chat_protocol: model.chat_protocol ?? "chat_completions",
    supports_vision: model.supports_vision,
    vision_format: model.vision_format,
    supports_tools: model.supports_tools,
    supports_forced_tool_choice: model.supports_forced_tool_choice,
    supports_reasoning: model.supports_reasoning,
    reasoning_effort: model.reasoning_effort ?? "",
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

  // 内置工具：逗号分隔的工具名；dict 形态（带参数）以 JSON 原文保留
  const [builtinToolsText, setBuiltinToolsText] = useState<string>(() =>
    (model.builtin_tools ?? [])
      .map((item) => (typeof item === "string" ? item : JSON.stringify(item)))
      .join(", "),
  );
  const [builtinToolsError, setBuiltinToolsError] = useState<string>();

  // 配置指纹：测试后任何变更都使结果过期（stale）
  const currentHash = fnv1a(JSON.stringify({ draft, jsonEnabled, jsonDrafts, builtinToolsText }));
  const [test, setTest] = useState<TestState>({ status: "idle" });
  const stale = test.status !== "idle" && test.hash !== undefined && test.hash !== currentHash;

  // ── 远程模型列表（打开对话框自动拉取一次 + 手动刷新） ─────────────
  const remoteQuery = useQuery({
    queryKey: ["remoteModels", pid],
    queryFn: () => providersApi.remoteModels(pid).then((r) => r.data.models),
    staleTime: 60_000,
    retry: false,
  });
  const remoteIds = useMemo(
    () => (remoteQuery.data ?? []).map((m) => m.id),
    [remoteQuery.data],
  );

  // ── 保存 / 保存并测试 ─────────────────────────────────────────────
  const updateMut = useMutation({
    mutationFn: (data: UpdateModelConfig) => modelsApi.update(model.id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["providerModels", pid] });
      qc.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  const parseJson = (field: JsonField): JsonObject | null => {
    if (!jsonEnabled[field]) return {};
    try {
      const value: unknown = JSON.parse(jsonDrafts[field] || "{}");
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

  /** 解析内置工具文本：非法 JSON 片段报错并返回 null */
  const parseBuiltinTools = (): Array<string | JsonObject> | null => {
    const tokens = builtinToolsText.split(",").map((s) => s.trim()).filter(Boolean);
    const items: Array<string | JsonObject> = [];
    for (const token of tokens) {
      if (token.startsWith("{")) {
        try {
          const value: unknown = JSON.parse(token);
          if (typeof value !== "object" || value === null || Array.isArray(value)) {
            setBuiltinToolsError(t("jsonObjectRequired"));
            return null;
          }
          items.push(value as JsonObject);
        } catch {
          setBuiltinToolsError(t("invalidJson"));
          return null;
        }
      } else {
        items.push(token);
      }
    }
    setBuiltinToolsError(undefined);
    return items;
  };

  /** 组装提交载荷；JSON 校验失败返回 null */
  const buildPayload = (): UpdateModelConfig | null => {
    const requestParams = parseJson("request_params");
    const extraBody = parseJson("extra_body");
    const extraHeaders = parseJson("extra_headers");
    const thinking = parseJson("thinking");
    const builtinTools = parseBuiltinTools();
    if (requestParams === null || extraBody === null || extraHeaders === null || thinking === null || builtinTools === null) return null;
    return {
      ...draft,
      request_params: requestParams,
      extra_body: extraBody,
      extra_headers: extraHeaders as Record<string, string>,
      thinking: thinking,
      builtin_tools: builtinTools,
    };
  };

  const handleSave = async () => {
    const payload = buildPayload();
    if (!payload) return;
    await updateMut.mutateAsync(payload);
    onClose();
  };

  const handleSaveAndTest = async () => {
    const payload = buildPayload();
    if (!payload) return;
    const hash = currentHash;
    await updateMut.mutateAsync(payload);
    setTest({ status: "running", hash });
    try {
      // 配置已落库，按 model_id 走真实流式链路测试
      const r = await modelsApi.testChat(pid, model.id);
      setTest({ status: r.data.ok ? "success" : "error", result: r.data, hash });
    } catch (e) {
      setTest({
        status: "error",
        result: { ok: false, error: e instanceof Error ? e.message : String(e) },
        hash,
      });
    }
  };

  const isCommon = provider.api_type === "openai" || provider.api_type === "anthropic";

  return (
    <Modal
      open
      onClose={onClose}
      width="max-w-2xl"
      title={
        <div className="flex items-center gap-2 min-w-0">
          <span className="truncate">{t("editModel")}</span>
          <span className={cn(
            "text-[10px] px-1.5 py-0.5 rounded-full border shrink-0",
            isCommon
              ? "bg-accent-subtle text-accent border-accent"
              : "bg-secondary text-muted border-border",
          )}>
            {provider.api_type}
          </span>
          <span className="text-xs text-muted font-normal truncate">{provider.name || pid}</span>
        </div>
      }
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button
            variant="secondary" size="sm"
            onClick={handleSaveAndTest}
            loading={test.status === "running"}
            disabled={updateMut.isPending}
            className="border-accent text-accent hover:bg-accent-subtle"
          >
            {test.status !== "running" && <FlaskConical size={12} />}
            {t("saveAndTest")}
          </Button>
          <Button
            variant="primary" size="sm"
            onClick={handleSave}
            loading={updateMut.isPending}
            disabled={test.status === "running"}
          >
            {t("common:save")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {/* 连接信息（供应商级，只读） */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("providerFields.base_url")}</label>
            <Input value={provider.base_url} readOnly className="opacity-70" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("providerFields.api_key")}</label>
            <Input type="password" value={provider.api_key} readOnly className="opacity-70" />
          </div>
        </div>
        <p className="text-[11px] text-muted opacity-70 -mt-1">{t("connManagedByProvider")}</p>

        {/* 模型标识 + 上下文/输出预算 */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("displayNameLabel")}</label>
            <Input value={model.id} readOnly className="opacity-70" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("modelIdLabel")}</label>
            <ModelCombobox
              value={draft.model ?? ""}
              onChange={(v) => patch({ model: v })}
              options={remoteIds}
              loading={remoteQuery.isFetching}
              onFetch={() => remoteQuery.refetch()}
              placeholder="例如：gpt-4.1 / claude-opus-5"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("modelFields.context_window")}</label>
            <Input
              type="number" min={0} step={1}
              value={draft.context_window ?? 0}
              placeholder={t("contextWindowPlaceholder")}
              onChange={(e) => patch({ context_window: Math.max(0, Number(e.target.value) || 0) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("modelFields.max_tokens")}</label>
            <Input
              type="number" min={0} step={1}
              value={draft.max_tokens ?? ""}
              placeholder={t("maxTokensPlaceholder")}
              onChange={(e) => patch({ max_tokens: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("reasoningEffort")}</label>
            <div className="flex gap-2 items-center">
              <label className="flex items-center gap-1.5 cursor-pointer shrink-0" title={t("effortHint")}>
                <input
                  type="checkbox"
                  checked={draft.supports_reasoning ?? false}
                  onChange={(e) => patch({ supports_reasoning: e.target.checked })}
                  className="accent-[rgb(168,85,247)] w-3.5 h-3.5"
                />
                <span className="text-xs text-foreground">{t("deepThinking")}</span>
              </label>
              {draft.supports_reasoning && (
                <Select
                  className="flex-1"
                  value={draft.reasoning_effort ?? ""}
                  onChange={(e) => patch({ reasoning_effort: e.target.value })}
                >
                  <option value="">{t("effortInherit")}</option>
                  <ReasoningEffortOptions t={t} />
                </Select>
              )}
            </div>
            <p className="text-[11px] text-muted opacity-70">{t("effortHint")}</p>
            {draft.supports_reasoning && (
              <JsonSection
                label={t("modelFields.thinking")}
                hint={t("modelFields.thinkingHint")}
                enabled={jsonEnabled.thinking}
                onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, thinking: v }))}
                value={jsonDrafts.thinking}
                onChange={(v) => { setJsonDrafts((p) => ({ ...p, thinking: v })); setJsonErrors((p) => ({ ...p, thinking: undefined })); }}
                error={jsonErrors.thinking}
              />
            )}
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("endpointLabel")}</label>
            <Select
              className="w-full"
              value={draft.chat_protocol ?? "chat_completions"}
              onChange={(e) => patch({ chat_protocol: e.target.value as ModelConfig["chat_protocol"] })}
            >
              <option value="chat_completions">/v1/chat/completions</option>
              <option value="responses">/v1/responses</option>
              <option value="auto">{t("chatProtocol.auto")}</option>
            </Select>
            <p className="text-[11px] text-muted opacity-70">{t("endpointAutoHint")}</p>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted" title={t("modelFields.temperatureHint")}>
              temperature
            </label>
            <Input
              type="number" step="any" min={0} max={2}
              value={draft.temperature ?? ""}
              placeholder={t("modelFields.temperatureHint")}
              onChange={(e) => patch({ temperature: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">top_p</label>
            <Input
              type="number" step="any" min={0} max={1}
              value={draft.top_p ?? ""}
              placeholder={t("modelFields.temperatureHint")}
              onChange={(e) => patch({ top_p: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </div>
        </div>

        {/* 供应商内置工具 */}
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted" title={t("modelFields.builtin_toolsHint")}>
            {t("modelFields.builtin_tools")}
          </label>
          <Input
            value={builtinToolsText}
            placeholder="web_search, code_interpreter"
            onChange={(e) => { setBuiltinToolsText(e.target.value); setBuiltinToolsError(undefined); }}
            className={cn("font-mono text-xs", builtinToolsError && "border-danger")}
            spellCheck={false}
          />
          <p className={cn("text-[11px]", builtinToolsError ? "text-danger" : "text-muted opacity-70")}>
            {builtinToolsError ?? t("modelFields.builtin_toolsHint")}
          </p>
        </div>

        {/* 可勾选启用的 JSON 区块 */}
        <JsonSection
          label={t("modelFields.extra_body")}
          hint={t("modelFields.extra_bodyHint")}
          enabled={jsonEnabled.extra_body}
          onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, extra_body: v }))}
          value={jsonDrafts.extra_body}
          onChange={(v) => { setJsonDrafts((p) => ({ ...p, extra_body: v })); setJsonErrors((p) => ({ ...p, extra_body: undefined })); }}
          error={jsonErrors.extra_body}
        />
        <JsonSection
          label={t("modelFields.extra_headers")}
          hint={t("modelFields.extra_headersHint")}
          enabled={jsonEnabled.extra_headers}
          onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, extra_headers: v }))}
          value={jsonDrafts.extra_headers}
          onChange={(v) => { setJsonDrafts((p) => ({ ...p, extra_headers: v })); setJsonErrors((p) => ({ ...p, extra_headers: undefined })); }}
          error={jsonErrors.extra_headers}
        />
        <JsonSection
          label={t("modelFields.request_params")}
          hint={t("modelFields.request_paramsHint")}
          enabled={jsonEnabled.request_params}
          onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, request_params: v }))}
          value={jsonDrafts.request_params}
          onChange={(v) => { setJsonDrafts((p) => ({ ...p, request_params: v })); setJsonErrors((p) => ({ ...p, request_params: undefined })); }}
          error={jsonErrors.request_params}
        />

        {/* 能力与类型 */}
        <div className="flex flex-wrap gap-3 items-center">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={draft.supports_vision ?? false}
              onChange={(e) => patch({ supports_vision: e.target.checked })}
              className="accent-accent2 w-3.5 h-3.5" />
            <span className="text-xs text-foreground">{t("vision")}</span>
          </label>
          {draft.supports_vision && (
            <Select
              value={draft.vision_format ?? "base64"}
              onChange={(e) => patch({ vision_format: e.target.value })}
              className="!h-7 text-xs"
            >
              <option value="base64">base64</option>
              <option value="url">url</option>
              <option value="both">both</option>
            </Select>
          )}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={draft.supports_tools ?? true}
              onChange={(e) => patch({ supports_tools: e.target.checked })}
              className="accent-accent w-3.5 h-3.5" />
            <span className="text-xs text-foreground">{t("toolCall")}</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer" title={t("forcedToolChoiceHint")}>
            <input type="checkbox" checked={draft.supports_forced_tool_choice ?? true}
              onChange={(e) => patch({ supports_forced_tool_choice: e.target.checked })}
              className="accent-accent w-3.5 h-3.5" />
            <span className="text-xs text-foreground">{t("forcedToolChoice")}</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer" title={t("enableHint")}>
            <Switch
              checked={draft.enabled ?? true}
              onChange={(v) => patch({ enabled: v })}
            />
            <span className="text-xs text-foreground">{t("enableLabel")}</span>
          </label>
        </div>

        <div>
          <p className="text-xs font-medium text-muted mb-1">{t("modelTypes")}</p>
          <div className="flex flex-wrap gap-1.5">
            {MODEL_TYPE_OPTIONS.map((mt) => {
              const active = (draft.model_types ?? []).includes(mt);
              return (
                <button
                  key={mt}
                  type="button"
                  onClick={() => {
                    const cur = draft.model_types ?? [];
                    patch({ model_types: active ? cur.filter((x) => x !== mt) : [...cur, mt] });
                  }}
                  className={cn(
                    "px-2.5 py-0.5 text-xs font-medium rounded-full border transition-all",
                    active ? "bg-accent-subtle text-accent border-accent" : "bg-secondary text-muted border-border",
                  )}
                >
                  {t(`modelTypeLabels.${mt}`, { defaultValue: mt })}
                </button>
              );
            })}
          </div>
        </div>

        {/* 测试结果卡片 */}
        {test.status !== "idle" && (
          <div className={cn(
            "rounded-md border p-3 text-xs space-y-1.5",
            test.status === "running" && "border-border bg-elevated",
            test.status === "success" && !stale && "border-[rgba(34,197,94,0.4)] bg-[rgba(34,197,94,0.06)]",
            test.status === "error" && !stale && "border-danger/40 bg-danger/5",
            stale && "border-[rgba(234,179,8,0.4)] bg-[rgba(234,179,8,0.06)]",
          )}>
            <div className="flex items-center gap-1.5 font-medium">
              {test.status === "running" && (
                <><Loader2 size={13} className="animate-spin text-muted" /><span className="text-muted">{t("testing")}</span></>
              )}
              {test.status === "success" && !stale && (
                <><CheckCircle2 size={13} className="text-[rgb(22,163,74)]" /><span className="text-[rgb(22,163,74)]">{t("testPassed")}</span></>
              )}
              {test.status === "error" && !stale && (
                <><XCircle size={13} className="text-danger" /><span className="text-danger">{t("testFailed")}</span></>
              )}
              {stale && (
                <><AlertTriangle size={13} className="text-[rgb(180,140,20)]" /><span className="text-[rgb(180,140,20)]">{t("testStale")}</span></>
              )}
            </div>
            {!stale && test.result?.ok && (
              <>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-foreground">
                  <span>{t("firstTokenLatency")}: {test.result.ttft_ms}ms</span>
                  <span>{t("totalDuration")}: {test.result.total_ms}ms</span>
                  <span>
                    {t("outputTokensLabel")}: {test.result.output_tokens}
                    {test.result.tokens_estimated && (
                      <span className="text-[rgb(180,140,20)]"> ({t("estimatedMark")})</span>
                    )}
                  </span>
                </div>
                {test.result.reply_preview && (
                  <p className="text-muted font-mono break-all">{test.result.reply_preview}</p>
                )}
              </>
            )}
            {!stale && test.result?.error && (
              <p className="text-danger break-all">{test.result.error}</p>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
