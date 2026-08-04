/**
 * DelegationPanel — 子代理模型分级管理（三挡）。
 *
 * 每挡手动添加已配置的 chat 模型（池内顺序即优先级）。委托执行时 AI 仅标注
 * 任务难度（delegate_task.difficulty），系统自动映射到对应挡位的可用模型；
 * 挡位空/全停用 → 降挡；仍未配置 → 子代理使用默认模型。
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";
import { modelsApi } from "@/lib/api";
import type { DelegationTiers, ModelPriorityItem } from "@/lib/types";
import { usePriorities } from "@/components/models/ModelSelect";

const TIERS = ["1", "2", "3"] as const;
type TierKey = (typeof TIERS)[number];

function TierSection({
  tier,
  items,
  candidates,
  onAdd,
  onRemove,
  onMove,
}: {
  tier: TierKey;
  items: ModelPriorityItem[];
  candidates: ModelPriorityItem[];
  onAdd: (tier: TierKey, modelId: string) => void;
  onRemove: (tier: TierKey, modelId: string) => void;
  onMove: (tier: TierKey, modelId: string, direction: -1 | 1) => void;
}) {
  const { t } = useTranslation("models");
  const [pick, setPick] = useState("");

  return (
    <div className="rounded-md border border-border bg-card p-3 md:p-4 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div>
          <span className="text-sm font-medium text-heading">{t(`delegation.tier${tier}`)}</span>
          <span className="ml-2 text-xs text-muted">{t(`delegation.tier${tier}Hint`)}</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <select
            value={pick}
            onChange={(e) => setPick(e.target.value)}
            className="bg-elevated border border-input rounded-md px-2 py-1.5 text-xs text-foreground outline-none focus:border-accent max-w-40"
          >
            <option value="">{t("delegation.selectModel")}</option>
            {candidates.map((c) => (
              <option key={c.id} value={c.id}>{c.id}</option>
            ))}
          </select>
          <button
            disabled={!pick}
            onClick={() => { onAdd(tier, pick); setPick(""); }}
            className="p-1.5 rounded-md border border-border text-muted hover:text-accent hover:border-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            title={t("delegation.addModel")}
          >
            <Plus size={14} />
          </button>
        </div>
      </div>

      {items.length === 0 ? (
        <p className="text-xs text-muted py-2">{t("delegation.tierEmpty")}</p>
      ) : (
        <div className="space-y-1.5">
          {items.map((item, idx) => (
            <div key={item.id} className="flex items-center gap-2 rounded-sm bg-secondary/50 px-2.5 py-1.5">
              <span className="text-xs font-mono text-muted w-4 shrink-0">{idx + 1}</span>
              <span className="text-sm text-foreground truncate flex-1 min-w-0">{item.id}</span>
              <span className="text-[10px] text-muted truncate max-w-28 hidden sm:inline">
                {item.provider_name}
              </span>
              {(item.input_cost != null || item.output_cost != null) && (
                <span className="text-[10px] text-muted shrink-0">
                  ${item.input_cost ?? "?"}/{item.output_cost ?? "?"}
                </span>
              )}
              {item.enabled === false && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-muted border border-border shrink-0">
                  {t("disabled")}
                </span>
              )}
              <button
                disabled={idx === 0}
                onClick={() => onMove(tier, item.id, -1)}
                className="p-1 rounded text-muted hover:text-foreground transition-colors disabled:opacity-30"
                title={t("moveUp")}
              >
                <ChevronUp size={13} />
              </button>
              <button
                disabled={idx === items.length - 1}
                onClick={() => onMove(tier, item.id, 1)}
                className="p-1 rounded text-muted hover:text-foreground transition-colors disabled:opacity-30"
                title={t("moveDown")}
              >
                <ChevronDown size={13} />
              </button>
              <button
                onClick={() => onRemove(tier, item.id)}
                className="p-1 rounded text-muted hover:text-danger transition-colors"
                title={t("delegation.remove")}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function DelegationPanel() {
  const { t } = useTranslation("models");
  const qc = useQueryClient();

  const { data: tiers = { 1: [], 2: [], 3: [] } } = useQuery<DelegationTiers>({
    queryKey: ["delegationTiers"],
    queryFn: () => modelsApi.delegationTiers().then((r) => r.data.tiers),
  });
  const { data: priorities = {} } = usePriorities();

  const setTierMut = useMutation({
    mutationFn: ({ tier, modelIds }: { tier: TierKey; modelIds: string[] }) =>
      modelsApi.setDelegationTier(Number(tier), modelIds),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["delegationTiers"] });
      qc.invalidateQueries({ queryKey: ["priorities"] });
    },
  });

  // 乐观更新池内列表，服务端落盘后 invalidate 对齐
  const applyTier = (tier: TierKey, next: ModelPriorityItem[]) => {
    qc.setQueryData<DelegationTiers>(["delegationTiers"], (old) => ({
      ...(old ?? { 1: [], 2: [], 3: [] }),
      [tier]: next,
    }));
    setTierMut.mutate({ tier, modelIds: next.map((i) => i.id) });
  };

  const handleAdd = (tier: TierKey, modelId: string) => {
    const item = (priorities.chat ?? []).find((i) => i.id === modelId);
    if (!item) return;
    const cur = tiers[tier] ?? [];
    if (cur.some((i) => i.id === modelId)) return;
    applyTier(tier, [...cur, item]);
  };

  const handleRemove = (tier: TierKey, modelId: string) => {
    applyTier(tier, (tiers[tier] ?? []).filter((i) => i.id !== modelId));
  };

  const handleMove = (tier: TierKey, modelId: string, direction: -1 | 1) => {
    const cur = [...(tiers[tier] ?? [])];
    const idx = cur.findIndex((i) => i.id === modelId);
    const to = idx + direction;
    const a = cur[idx];
    const b = cur[to];
    if (idx < 0 || !a || !b) return;
    cur[idx] = b;
    cur[to] = a;
    applyTier(tier, cur);
  };

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <p className="text-sm text-muted">{t("delegation.desc")}</p>
        <p className="text-xs text-muted">{t("delegation.fallbackHint")}</p>
      </div>

      {TIERS.map((tier) => {
        const inTier = new Set((tiers[tier] ?? []).map((i) => i.id));
        const candidates = (priorities.chat ?? []).filter(
          (i) => i.enabled !== false && !inTier.has(i.id),
        );
        return (
          <TierSection
            key={tier}
            tier={tier}
            items={tiers[tier] ?? []}
            candidates={candidates}
            onAdd={handleAdd}
            onRemove={handleRemove}
            onMove={handleMove}
          />
        );
      })}
    </div>
  );
}
