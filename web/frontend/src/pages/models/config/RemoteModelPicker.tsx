import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Plus, Search } from "lucide-react";
import { providersApi, type RemoteModelInfo } from "@/lib/api";
import type { CreateModelConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, Input, LoadingBlock } from "@/components/ui";

/** 浏览远程模型并批量添加 */
export function RemoteModelPicker({
  providerId,
  apiType,
  onAdd,
  isAdding,
  onAddingChange,
}: {
  providerId: string;
  apiType: string;
  onAdd: (data: CreateModelConfig) => Promise<void>;
  isAdding: boolean;
  onAddingChange: (adding: boolean) => void;
}) {
  const { t } = useTranslation("models");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isFetching } = useQuery<{ models: RemoteModelInfo[] }>({
    queryKey: ["remoteModels", providerId],
    queryFn: () => providersApi.remoteModels(providerId).then((r) => r.data),
    staleTime: 60_000,
  });

  const remoteModels = data?.models ?? [];
  const filtered = filter
    ? remoteModels.filter((m) => m.id.toLowerCase().includes(filter.toLowerCase()))
    : remoteModels;

  const toggle = (modelId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(modelId)) next.delete(modelId);
      else next.add(modelId);
      return next;
    });
  };

  const handleAddSelected = async () => {
    if (selected.size === 0) return;
    onAddingChange(true);
    try {
      for (const modelId of selected) {
        const shortName = modelId.split("/").pop() || modelId;
        let supportsVision = false;
        let supportsTools = true;
        let contextWindow = 0;

        try {
          const infoRes = await providersApi.modelInfo(modelId, apiType);
          const info = infoRes.data;
          if (info.found) {
            supportsVision = info.supports_vision ?? false;
            supportsTools = info.supports_tools ?? true;
            contextWindow = info.max_input_tokens ?? 0;
          }
        } catch { /* litellm 不认识的模型用默认值 */ }

        await onAdd({
          id: shortName,
          model: modelId,
          model_types: ["chat"],
          temperature: 0.7,
          top_p: 1.0,
          context_window: contextWindow,
          frequency_penalty: 0,
          presence_penalty: 0,
          supports_tools: supportsTools,
          supports_vision: supportsVision,
          supports_forced_tool_choice: true,
          vision_format: "base64",
          supports_reasoning: false,
          timeout: 120.0,
          chat_protocol: "chat_completions",
          request_params: {},
          extra_body: {},
        });
      }
      setSelected(new Set());
    } finally {
      onAddingChange(false);
    }
  };

  return (
    <div className="p-4 rounded-md border border-accent bg-elevated space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-heading">{t("remoteModelsTitle")}</p>
        <span className="text-xs text-muted">
          {isFetching ? t("loading") : t("remoteCount", { count: remoteModels.length })}
        </span>
      </div>

      {isFetching && <LoadingBlock label={t("loading")} />}

      {!isFetching && remoteModels.length > 0 && (
        <>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("filterModels")}
              className="pl-9"
            />
          </div>

          <div className="max-h-64 overflow-y-auto space-y-1">
            {filtered.map((rm) => (
              <label
                key={rm.id}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 rounded-md cursor-pointer transition-all",
                  rm.already_added
                    ? "opacity-50 cursor-default bg-secondary"
                    : selected.has(rm.id)
                      ? "bg-accent-subtle border border-accent"
                      : "hover:bg-hover border border-transparent",
                )}
              >
                <input
                  type="checkbox"
                  checked={rm.already_added || selected.has(rm.id)}
                  disabled={rm.already_added}
                  onChange={() => !rm.already_added && toggle(rm.id)}
                  className="accent-accent w-3.5 h-3.5 shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-foreground truncate block">{rm.id}</span>
                  {rm.owned_by && <span className="text-[10px] text-muted">{rm.owned_by}</span>}
                </div>
                {rm.already_added && (
                  <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-accent-subtle text-accent">
                    <Check size={9} /> {t("alreadyAdded")}
                  </span>
                )}
              </label>
            ))}
          </div>

          <div className="flex items-center justify-between pt-2 border-t border-border">
            <span className="text-xs text-muted">
              {t("selectedCount", { count: selected.size })}
            </span>
            <Button
              variant="primary"
              onClick={handleAddSelected}
              disabled={selected.size === 0}
              loading={isAdding}
            >
              <Plus size={14} /> {t("addSelected")}
            </Button>
          </div>
        </>
      )}

      {!isFetching && remoteModels.length === 0 && (
        <p className="text-sm text-muted py-4 text-center">{t("noRemoteModels")}</p>
      )}
    </div>
  );
}
