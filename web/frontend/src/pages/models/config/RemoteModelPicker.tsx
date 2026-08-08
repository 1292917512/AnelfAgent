import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Eye, Layers, Plus, RefreshCw, Search, Wrench } from "lucide-react";
import { providersApi, type RemoteModelInfo } from "@/lib/api";
import type { CreateModelConfig, ModelInfoResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, Input, LoadingBlock } from "@/components/ui";

/** 有限并发映射：批量添加时最多 3 个并发请求，避免打满连接 */
async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let idx = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (idx < items.length) {
      const cur = idx++;
      results[cur] = await fn(items[cur]!, cur);
    }
  });
  await Promise.all(workers);
  return results;
}

/** 远程模型 id 的本地短名（与添加时的配置 id 一致） */
function shortNameOf(modelId: string): string {
  return modelId.split("/").pop() || modelId;
}

function formatContext(tokens: number | undefined): string | null {
  if (!tokens) return null;
  return tokens >= 1000 ? `${Math.round(tokens / 1000)}K` : String(tokens);
}

/** 浏览远程模型并批量添加 */
export function RemoteModelPicker({
  providerId,
  apiType,
  existingIds,
  onAdd,
  isAdding,
  onAddingChange,
}: {
  providerId: string;
  apiType: string;
  /** 已配置的模型 id 集合：already_added 同时按远程 id 与本地短名判断 */
  existingIds: string[];
  onAdd: (data: CreateModelConfig) => Promise<void>;
  isAdding: boolean;
  onAddingChange: (adding: boolean) => void;
}) {
  const { t } = useTranslation("models");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [onlyUnadded, setOnlyUnadded] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [summary, setSummary] = useState("");

  const existing = useMemo(() => new Set(existingIds), [existingIds]);
  const isAdded = (id: string) => existing.has(id) || existing.has(shortNameOf(id));

  const modelsQuery = useQuery<{ models: RemoteModelInfo[] }>({
    queryKey: ["remoteModels", providerId],
    queryFn: () => providersApi.remoteModels(providerId).then((r) => r.data),
    staleTime: 60_000,
    retry: false,
  });
  const remoteModels = modelsQuery.data?.models ?? [];

  // 批量能力信息：litellm 本地表一次往返，供行内徽标与添加时回填
  const infoQuery = useQuery({
    queryKey: ["remoteModelInfo", providerId, apiType, remoteModels.length],
    queryFn: () =>
      providersApi.modelInfoBatch(remoteModels.map((m) => m.id), apiType).then((r) => r.data.info),
    enabled: remoteModels.length > 0,
    staleTime: 300_000,
    retry: false,
  });
  const infoMap: Record<string, ModelInfoResult> = infoQuery.data ?? {};

  const filtered = useMemo(() => {
    const kw = filter.trim().toLowerCase();
    return remoteModels.filter((m) => {
      if (onlyUnadded && isAdded(m.id)) return false;
      return !kw || m.id.toLowerCase().includes(kw);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remoteModels, filter, onlyUnadded, existing]);

  const selectable = filtered.filter((m) => !isAdded(m.id));

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
    setSummary("");
    const items = [...selected];
    let ok = 0;
    const failures: string[] = [];
    try {
      await mapLimit(items, 3, async (modelId, i) => {
        const info = infoMap[modelId];
        try {
          await onAdd({
            id: shortNameOf(modelId),
            model: modelId,
            context_window: info?.found ? info.max_input_tokens ?? 0 : 0,
            supports_tools: info?.found ? info.supports_tools ?? true : true,
            supports_vision: info?.found ? info.supports_vision ?? false : false,
          });
          ok += 1;
        } catch (e) {
          // 单点失败（如 id 冲突）不中断整批，汇总报告
          const detail =
            (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
          failures.push(`${shortNameOf(modelId)}: ${typeof detail === "string" ? detail : String(e)}`);
        }
        setProgress({ done: i + 1, total: items.length });
      });
      setSelected(new Set());
      setSummary(
        failures.length === 0
          ? t("addSummaryOk", { count: ok })
          : t("addSummaryPartial", { ok, fail: failures.length, first: failures[0] }),
      );
    } finally {
      setProgress(null);
      onAddingChange(false);
    }
  };

  return (
    <div className="p-4 rounded-md border border-accent bg-elevated space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-heading">{t("remoteModelsTitle")}</p>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">
            {modelsQuery.isFetching ? t("loading") : t("remoteCount", { count: remoteModels.length })}
          </span>
          <button
            type="button"
            onClick={() => modelsQuery.refetch()}
            className="p-1 rounded text-muted hover:text-accent transition-colors"
            title={t("common:refresh", { defaultValue: "刷新" })}
          >
            <RefreshCw size={12} className={cn(modelsQuery.isFetching && "animate-spin")} />
          </button>
        </div>
      </div>

      {modelsQuery.isFetching && <LoadingBlock label={t("loading")} />}

      {/* 拉取失败：显示原因 + 重试，而非静默空白 */}
      {modelsQuery.isError && (
        <div className="p-3 rounded-md border border-danger/40 bg-danger/5 space-y-2">
          <p className="text-xs text-danger break-all">
            {t("noRemoteModels")}
            {modelsQuery.error instanceof Error && `: ${modelsQuery.error.message}`}
          </p>
          <Button variant="secondary" size="sm" onClick={() => modelsQuery.refetch()}>
            <RefreshCw size={12} /> {t("common:retry", { defaultValue: "重试" })}
          </Button>
        </div>
      )}

      {!modelsQuery.isFetching && !modelsQuery.isError && remoteModels.length > 0 && (
        <>
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={t("filterModels")}
                className="pl-9"
              />
            </div>
            <label className="flex items-center gap-1.5 cursor-pointer shrink-0">
              <input
                type="checkbox"
                checked={onlyUnadded}
                onChange={(e) => setOnlyUnadded(e.target.checked)}
                className="accent-accent w-3.5 h-3.5"
              />
              <span className="text-xs text-muted">{t("onlyUnadded")}</span>
            </label>
          </div>

          <div className="max-h-64 overflow-y-auto space-y-1">
            {filtered.map((rm) => {
              const added = isAdded(rm.id);
              const info = infoMap[rm.id];
              const ctx = info?.found ? formatContext(info.max_input_tokens) : null;
              return (
                <label
                  key={rm.id}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2 rounded-md cursor-pointer transition-all",
                    added
                      ? "opacity-50 cursor-default bg-secondary"
                      : selected.has(rm.id)
                        ? "bg-accent-subtle border border-accent"
                        : "hover:bg-hover border border-transparent",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={added || selected.has(rm.id)}
                    disabled={added}
                    onChange={() => !added && toggle(rm.id)}
                    className="accent-accent w-3.5 h-3.5 shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm text-foreground truncate block">{rm.id}</span>
                    {rm.owned_by && <span className="text-[10px] text-muted">{rm.owned_by}</span>}
                  </div>
                  {/* 行内能力元数据（litellm 本地表） */}
                  <span className="flex items-center gap-1 shrink-0">
                    {info?.found && info.supports_vision && (
                      <Eye size={11} className="text-accent2" />
                    )}
                    {info?.found && info.supports_tools === false && (
                      <Wrench size={11} className="text-muted opacity-40" />
                    )}
                    {ctx && (
                      <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(234,179,8,0.1)] text-[rgb(180,140,20)] border border-[rgba(234,179,8,0.25)]">
                        <Layers size={9} /> {ctx}
                      </span>
                    )}
                  </span>
                  {added && (
                    <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-accent-subtle text-accent shrink-0">
                      <Check size={9} /> {t("alreadyAdded")}
                    </span>
                  )}
                </label>
              );
            })}
            {filtered.length === 0 && (
              <p className="text-xs text-muted py-3 text-center">{t("noFilterMatch")}</p>
            )}
          </div>

          <div className="flex items-center justify-between pt-2 border-t border-border gap-2 flex-wrap">
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="text-xs text-accent hover:underline"
                onClick={() => setSelected(new Set(selectable.map((m) => m.id)))}
              >
                {t("selectAllFiltered", { count: selectable.length })}
              </button>
              {selected.size > 0 && (
                <button
                  type="button"
                  className="text-xs text-muted hover:underline"
                  onClick={() => setSelected(new Set())}
                >
                  {t("common:clear", { defaultValue: "清空" })}
                </button>
              )}
              <span className="text-xs text-muted">
                {progress
                  ? t("addingProgress", { done: progress.done, total: progress.total })
                  : t("selectedCount", { count: selected.size })}
              </span>
            </div>
            <Button
              variant="primary"
              onClick={handleAddSelected}
              disabled={selected.size === 0}
              loading={isAdding}
            >
              <Plus size={14} /> {t("addSelected")}
            </Button>
          </div>

          {summary && (
            <p className="text-xs text-muted break-all">{summary}</p>
          )}
        </>
      )}

      {!modelsQuery.isFetching && !modelsQuery.isError && remoteModels.length === 0 && (
        <p className="text-sm text-muted py-4 text-center">{t("noRemoteModels")}</p>
      )}
    </div>
  );
}
