/**
 * BuiltinTierSection — 内置难度档（easy/medium/hard）的候选池管理。
 *
 * delegate_task 的 difficulty 1/2/3 是这三个内置档案的语法糖；池内顺序即优先级，
 * 前面的模型不可用时依次回退，本档全不可用降挡到低档。与自定义档案共用
 * 统一注册表与同一套 CRUD（update_sub_agent 的 models 整体替换）。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";
import type { ModelPriorityItem, SubAgentProfile } from "@/lib/types";

const TIER_LABEL_KEY: Record<number, string> = { 1: "tier1", 2: "tier2", 3: "tier3" };

/** 单个模型的池内行（详情来自 chat 优先级表 join） */
function PoolRow({
  modelId,
  idx,
  total,
  detail,
  onMove,
  onRemove,
}: {
  modelId: string;
  idx: number;
  total: number;
  detail: ModelPriorityItem | undefined;
  onMove: (direction: -1 | 1) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation("models");
  return (
    <div className="flex items-center gap-2 rounded-sm bg-secondary/50 px-2.5 py-1.5">
      <span className="text-xs font-mono text-muted w-4 shrink-0">{idx + 1}</span>
      <span className="text-sm text-foreground truncate flex-1 min-w-0">{modelId}</span>
      <span className="text-[10px] text-muted truncate max-w-28 hidden sm:inline">
        {detail?.provider_name}
      </span>
      {(detail?.input_cost != null || detail?.output_cost != null) && (
        <span className="text-[10px] text-muted shrink-0">
          ${detail?.input_cost ?? "?"}/{detail?.output_cost ?? "?"}
        </span>
      )}
      {detail?.enabled === false ? (
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-muted border border-border shrink-0">
          {t("subagents.fallbackNext")}
        </span>
      ) : !detail ? (
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-danger border border-border shrink-0">
          {t("subagents.modelMissing")}
        </span>
      ) : null}
      <button
        disabled={idx === 0}
        onClick={() => onMove(-1)}
        className="p-1 rounded text-muted hover:text-foreground transition-colors disabled:opacity-30"
        title={t("moveUp")}
      >
        <ChevronUp size={13} />
      </button>
      <button
        disabled={idx === total - 1}
        onClick={() => onMove(1)}
        className="p-1 rounded text-muted hover:text-foreground transition-colors disabled:opacity-30"
        title={t("moveDown")}
      >
        <ChevronDown size={13} />
      </button>
      <button
        onClick={onRemove}
        className="p-1 rounded text-muted hover:text-danger transition-colors"
        title={t("subagents.remove")}
      >
        <X size={13} />
      </button>
    </div>
  );
}

export function BuiltinTierSection({
  profile,
  chatItems,
  onUpdate,
}: {
  profile: SubAgentProfile;
  chatItems: ModelPriorityItem[];
  onUpdate: (name: string, data: { models: string[]; description?: string }) => void;
}) {
  const { t } = useTranslation("models");
  const [pick, setPick] = useState("");

  const inPool = new Set(profile.models);
  const candidates = chatItems.filter((i) => i.enabled !== false && !inPool.has(i.id));
  const detailOf = (id: string) => chatItems.find((i) => i.id === id);

  const updatePool = (next: string[]) => onUpdate(profile.name, { models: next });
  const move = (modelId: string, direction: -1 | 1) => {
    const cur = [...profile.models];
    const idx = cur.indexOf(modelId);
    const to = idx + direction;
    const a = cur[idx];
    const b = cur[to];
    if (idx < 0 || a === undefined || b === undefined) return;
    cur[idx] = b;
    cur[to] = a;
    updatePool(cur);
  };

  const labelKey = TIER_LABEL_KEY[profile.tier] ?? "tierCustom";

  return (
    <div className="rounded-md border border-border bg-card p-3 md:p-4 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-heading">{t(`subagents.${labelKey}`)}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-accent-subtle text-accent font-mono shrink-0">
              {profile.name}
            </span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-muted border border-border shrink-0">
              {t("subagents.builtinBadge")}
            </span>
          </div>
          <span className="text-xs text-muted">{t(`subagents.${labelKey}Hint`)}</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <select
            value={pick}
            onChange={(e) => setPick(e.target.value)}
            className="bg-elevated border border-input rounded-md px-2 py-1.5 text-xs text-foreground outline-none focus:border-accent max-w-40"
          >
            <option value="">{t("subagents.selectModel")}</option>
            {candidates.map((c) => (
              <option key={c.id} value={c.id}>{c.id}</option>
            ))}
          </select>
          <button
            disabled={!pick}
            onClick={() => {
              updatePool([...profile.models, pick]);
              setPick("");
            }}
            className="p-1.5 rounded-md border border-border text-muted hover:text-accent hover:border-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            title={t("subagents.addToPool")}
          >
            <Plus size={14} />
          </button>
        </div>
      </div>

      {profile.models.length === 0 ? (
        <p className="text-xs text-muted py-2">{t("subagents.poolEmpty")}</p>
      ) : (
        <div className="space-y-1.5">
          {profile.models.map((modelId, idx) => (
            <PoolRow
              key={modelId}
              modelId={modelId}
              idx={idx}
              total={profile.models.length}
              detail={detailOf(modelId)}
              onMove={(d) => move(modelId, d)}
              onRemove={() => updatePool(profile.models.filter((m) => m !== modelId))}
            />
          ))}
        </div>
      )}
    </div>
  );
}
